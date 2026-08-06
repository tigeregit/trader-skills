"""push2 能力（stock_info / concept_blocks）服务端测试。

验证东财 push2 单端点能力的上游知识下沉：
  - secid 市场前缀（6/9→sh=1，其余 sz=0）
  - stock_info：stock/get + f 字段表映射（f57→code 等）
  - concept_blocks：slist/get + diff 数组解析
  - 字段映射在服务端（客户端拿结构化数据，零上游知识）
  - 403 触发熔断
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from asgk_server.server import CapabilityServer


def _config() -> dict:
    return {
        "group": [{"name": "eastmoney", "domains": ["push2.eastmoney.com"],
                   "rps": 100, "jitter": [0, 0]}],
        "circuit": {"cooldown_seconds": 300, "failure_threshold": 3,
                    "probe_lease_seconds": 120},
        "state": {"enabled": False},
        "retry": {"max_attempts": 1},
        "cache": {"session": {"intraday_start": "09:00", "intraday_end": "18:00"},
                  "persist": {"enabled": False}},
        "fingerprint": {"enabled": False},
    }


@pytest.fixture
def srv() -> CapabilityServer:
    s = CapabilityServer(_config())
    yield s
    s.close()


def _resp(data, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = data
    return r


# ── stock_info ───────────────────────────────────────────────
class TestStockInfo:
    def test_returns_structured_dict(self, srv):
        """f 字段映射下沉：f57→code, f58→name 等。客户端拿结构化 dict。"""
        em_data = {"data": {"f57": "600519", "f58": "贵州茅台", "f127": "白酒",
                            "f84": 12.56e8, "f85": 12.56e8, "f116": 1.6e12,
                            "f117": 1.6e12, "f189": "20010827", "f43": 1308.55}}
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp(em_data)):
            status, payload = srv.handle_capability(
                "stock_info", {"code": "600519"})
        assert status == 200
        d = payload["data"]
        assert d["code"] == "600519"
        assert d["name"] == "贵州茅台"
        assert d["industry"] == "白酒"
        assert d["price"] == 1308.55
        assert d["list_date"] == "20010827"

    def test_secid_market_prefix_sh(self, srv):
        """6 开头 → secid 1.600519（sh）。"""
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp({"data": {}})) as m:
            srv.handle_capability("stock_info", {"code": "600519"})
        assert m.call_args.kwargs["params"]["secid"] == "1.600519"

    def test_secid_market_prefix_sz(self, srv):
        """0/3 开头 → secid 0.000001（sz）。"""
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp({"data": {}})) as m:
            srv.handle_capability("stock_info", {"code": "000001"})
        assert m.call_args.kwargs["params"]["secid"] == "0.000001"

    def test_fields_param(self, srv):
        """f 字段表完整传递。"""
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp({"data": {}})) as m:
            srv.handle_capability("stock_info", {"code": "600519"})
        fields = m.call_args.kwargs["params"]["fields"]
        for f in ["f57", "f58", "f84", "f85", "f127", "f116", "f117", "f189", "f43"]:
            assert f in fields

    def test_empty_data_returns_zeroes(self, srv):
        """data 为空 dict 时返回零值（不崩）。"""
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp({"data": {}})):
            status, payload = srv.handle_capability(
                "stock_info", {"code": "600519"})
        d = payload["data"]
        assert d["code"] == ""
        assert d["price"] == 0


# ── concept_blocks ───────────────────────────────────────────
class TestConceptBlocks:
    def test_returns_structured_boards(self, srv):
        """diff 数组解析下沉：f14→name, f12→code 等。"""
        em_data = {"data": {"diff": [
            {"f12": "BK0477", "f14": "白酒", "f3": 1.5, "f128": "贵州茅台"},
            {"f12": "BK0733", "f14": "超级品牌", "f3": 0.8, "f128": "格力电器"},
        ]}}
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp(em_data)):
            status, payload = srv.handle_capability(
                "concept_blocks", {"code": "600519"})
        d = payload["data"]
        assert d["total"] == 2
        assert len(d["boards"]) == 2
        assert d["boards"][0] == {"name": "白酒", "code": "BK0477",
                                  "change_pct": 1.5, "lead_stock": "贵州茅台"}
        assert "白酒" in d["concept_tags"]

    def test_diff_as_list(self, srv):
        """diff 是 list 形态也能解析。"""
        em_data = {"data": {"diff": [{"f12": "BK1", "f14": "X", "f3": 0, "f128": ""}]}}
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp(em_data)):
            status, payload = srv.handle_capability(
                "concept_blocks", {"code": "600519"})
        assert payload["data"]["total"] == 1

    def test_empty_diff_returns_empty(self, srv):
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp({"data": {}})):
            status, payload = srv.handle_capability(
                "concept_blocks", {"code": "600519"})
        assert payload["data"]["total"] == 0
        assert payload["data"]["boards"] == []

    def test_secid_and_spt(self, srv):
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp({"data": {}})) as m:
            srv.handle_capability("concept_blocks", {"code": "600519"})
        params = m.call_args.kwargs["params"]
        assert params["secid"] == "1.600519"
        assert params["spt"] == "3"
        assert params["pz"] == "200"


# ── 熔断（共享 _egress_get）──────────────────────────────────
class TestCircuit:
    def test_403_triggers_circuit(self, srv):
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp({}, status=403)):
            status1, _ = srv.handle_capability(
                "stock_info", {"code": "600519"})
        assert status1 == 403
        with patch("asgk_server.capabilities.push2.egress_request") as m:
            status2, _ = srv.handle_capability(
                "stock_info", {"code": "600519"})
        assert status2 == 503
        m.assert_not_called()
