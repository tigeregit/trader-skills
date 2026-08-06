"""emquery 能力服务端测试（mock 东财 push2 上游）。

验证 §3.4 em_get 枢纽：通用 URL 查询能力正确下沉到服务端。
  - GET 请求：url + params 透传，返回解析后的 JSON
  - POST 请求：json body / form body / 空 body
  - 域名归组：push2→eastmoney 组限流（按 URL host 动态归组，非 SourceMeta 固定）
  - 未知域名拒绝（fail-closed）
  - 403 触发熔断
  - 字段映射留客户端（emquery 返回原始 JSON）

不打真实东财——mock asgk_server.capabilities.emquery.egress_request。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from asgk_server.server import CapabilityServer


def _config() -> dict:
    return {
        "group": [
            {"name": "eastmoney", "domains": ["push2.eastmoney.com",
                                              "push2ex.eastmoney.com"],
             "rps": 100, "jitter": [0, 0]},
            {"name": "10jqka", "domains": ["data.hexin.cn"],
             "rps": 100, "jitter": [0, 0]},
        ],
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
    r.text = ""
    return r


# ── GET 查询 ─────────────────────────────────────────────────
class TestGetQuery:
    def test_get_returns_parsed_json(self, srv):
        """emquery GET 返回 r.json() 的结果（字段映射留客户端）。"""
        em_data = {"data": {"f57": "600519", "f58": "贵州茅台", "f43": 1308}}
        with patch("asgk_server.capabilities.emquery.egress_request",
                   return_value=_resp(em_data)) as m:
            status, payload = srv.handle_capability("emquery", {
                "url": "https://push2.eastmoney.com/api/qt/stock/get",
                "params": {"secid": "1.600519", "fields": "f57,f58,f43"},
            })
        assert status == 200
        assert payload["data"] == em_data
        _method, _client, url = m.call_args.args
        kwargs = m.call_args.kwargs
        assert url == "https://push2.eastmoney.com/api/qt/stock/get"
        assert kwargs["params"]["secid"] == "1.600519"

    def test_server_provides_default_ua(self, srv):
        """服务端统一持有 UA，客户端不传。"""
        with patch("asgk_server.capabilities.emquery.egress_request",
                   return_value=_resp({})) as m:
            srv.handle_capability("emquery", {
                "url": "https://push2.eastmoney.com/api/qt/stock/get",
            })
        assert "User-Agent" in m.call_args.kwargs["headers"]

    def test_client_headers_merged(self, srv):
        """客户端传的 headers（如 Referer）合并到出网请求。"""
        with patch("asgk_server.capabilities.emquery.egress_request",
                   return_value=_resp({})) as m:
            srv.handle_capability("emquery", {
                "url": "https://push2.eastmoney.com/api/qt/stock/get",
                "headers": {"Referer": "https://quote.eastmoney.com/"},
            })
        assert m.call_args.kwargs["headers"]["Referer"] == "https://quote.eastmoney.com/"


# ── 域名归组 ─────────────────────────────────────────────────
class TestDomainGrouping:
    def test_eastmoney_domain_uses_eastmoney_group(self, srv):
        """push2.eastmoney.com → eastmoney 限流组。"""
        with patch("asgk_server.capabilities.emquery.egress_request",
                   return_value=_resp({})) as m:
            srv.handle_capability("emquery", {
                "url": "https://push2.eastmoney.com/api/qt/stock/get",
            })
        # acquire 被调（说明走了限流组）；验证 bucket 计数
        assert srv.group_reqs["eastmoney"] >= 1

    def test_hexin_domain_uses_10jqka_group(self, srv):
        """data.hexin.cn → 10jqka 限流组（按 URL host 动态归组）。"""
        with patch("asgk_server.capabilities.emquery.egress_request",
                   return_value=_resp({})):
            srv.handle_capability("emquery", {
                "url": "https://data.hexin.cn/market/hsgtApi/method/dayChart/",
            })
        assert srv.group_reqs["10jqka"] >= 1

    def test_unknown_domain_rejected(self, srv):
        """未知域名（无对应限流组）拒绝出网（fail-closed）。"""
        with patch("asgk_server.capabilities.emquery.egress_request") as m:
            status, payload = srv.handle_capability("emquery", {
                "url": "https://evil.example.com/api",
            })
        assert status == 400
        assert "无对应限流组" in payload["error"]
        m.assert_not_called()


# ── POST 查询 ────────────────────────────────────────────────
class TestPostQuery:
    def test_post_json_body(self, srv):
        with patch("asgk_server.capabilities.emquery.egress_request",
                   return_value=_resp({"ok": True})) as m:
            srv.handle_capability("emquery", {
                "url": "https://push2.eastmoney.com/api/qt/stock/get",
                "method": "POST", "body": {"k": "v"}, "body_type": "json",
            })
        assert m.call_args.args[0] == "post"
        assert m.call_args.kwargs["json"] == {"k": "v"}

    def test_post_form_body(self, srv):
        with patch("asgk_server.capabilities.emquery.egress_request",
                   return_value=_resp({"ok": True})) as m:
            srv.handle_capability("emquery", {
                "url": "https://push2.eastmoney.com/api/qt/stock/get",
                "method": "POST", "body": {"k": "v"}, "body_type": "form",
            })
        assert m.call_args.kwargs["data"] == {"k": "v"}

    def test_post_empty_body(self, srv):
        with patch("asgk_server.capabilities.emquery.egress_request",
                   return_value=_resp({"ok": True})) as m:
            srv.handle_capability("emquery", {
                "url": "https://push2.eastmoney.com/api/qt/stock/get",
                "method": "POST",
            })
        assert m.call_args.args[0] == "post"


# ── 熔断 ─────────────────────────────────────────────────────
class TestCircuit:
    def test_403_triggers_eastmoney_circuit(self, srv):
        with patch("asgk_server.capabilities.emquery.egress_request",
                   return_value=_resp({}, status=403)):
            status1, _ = srv.handle_capability("emquery", {
                "url": "https://push2.eastmoney.com/api/qt/stock/get",
            })
        assert status1 == 403
        # eastmoney 组熔断已开
        with patch("asgk_server.capabilities.emquery.egress_request") as m:
            status2, _ = srv.handle_capability("emquery", {
                "url": "https://push2.eastmoney.com/api/qt/stock/get",
            })
        assert status2 == 503
        m.assert_not_called()

    def test_403_on_hexin_only_blocks_10jqka(self, srv):
        """hexin 的 403 只熔断 10jqka 组，不影响 eastmoney 组。"""
        with patch("asgk_server.capabilities.emquery.egress_request",
                   return_value=_resp({}, status=403)):
            srv.handle_capability("emquery", {
                "url": "https://data.hexin.cn/market/hsgtApi/method/dayChart/",
            })
        # eastmoney 组仍可用
        with patch("asgk_server.capabilities.emquery.egress_request",
                   return_value=_resp({"ok": True})):
            status, payload = srv.handle_capability("emquery", {
                "url": "https://push2.eastmoney.com/api/qt/stock/get",
            })
        assert status == 200


# ── 容错 ─────────────────────────────────────────────────────
class TestEdgeCases:
    def test_non_json_returns_raw_text(self, srv):
        """非 JSON 响应包装为 {_raw_text: ...}（不崩）。"""
        r = MagicMock()
        r.status_code = 200
        r.json.side_effect = ValueError("not json")
        r.text = "plain text response"
        with patch("asgk_server.capabilities.emquery.egress_request",
                   return_value=r):
            status, payload = srv.handle_capability("emquery", {
                "url": "https://push2.eastmoney.com/api/qt/stock/get",
            })
        assert status == 200
        assert payload["data"] == {"_raw_text": "plain text response"}

    def test_network_error_returns_none(self, srv):
        """网络异常 → fetch 返回 None → 502。"""
        with patch("asgk_server.capabilities.emquery.egress_request",
                   side_effect=ConnectionError("network down")):
            status, payload = srv.handle_capability("emquery", {
                "url": "https://push2.eastmoney.com/api/qt/stock/get",
            })
        assert status == 502
