"""limitup_pool 能力服务端测试（mock 东财 push2ex 上游）。

验证东财涨停四池（zt/zb/dt/yzt）的查询机制下沉到服务端：
  - pool_type 参数驱动 endpoint + sort 选择（zt→getTopicZTPool/fbt:asc 等）
  - ut 常量 / dpt / pagesize / Referer 等参数构造与 asgk 一致
  - result.data.pool 数组解析（data 为 null = 非交易日 → 空）
  - 403 触发熔断
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from asgk_server.server import CapabilityServer


def _config() -> dict:
    return {
        "group": [{"name": "eastmoney", "domains": ["push2ex.eastmoney.com"],
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


def _resp(pool: list, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"data": {"pool": pool}}
    return r


def _resp_no_data(status: int = 200) -> MagicMock:
    """data 为 null（非交易日）。"""
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"data": None}
    return r


# ── pool_type → endpoint 映射 ────────────────────────────────
class TestPoolTypeRouting:
    @pytest.mark.parametrize("pool_type,endpoint,sort", [
        ("zt", "getTopicZTPool", "fbt:asc"),
        ("zb", "getTopicZBPool", "fbt:asc"),
        ("dt", "getTopicDTPool", "fund:asc"),
        ("yzt", "getYesterdayZTPool", "zs:desc"),
    ])
    def test_endpoint_per_pool_type(self, srv, pool_type, endpoint, sort):
        with patch("asgk_server.capabilities.limitup_pool.egress_request",
                   return_value=_resp([])) as m:
            srv.handle_capability("limitup_pool",
                                  {"pool_type": pool_type, "date": "20260806"})
        _method, _client, url = m.call_args.args
        kwargs = m.call_args.kwargs
        assert url == f"https://push2ex.eastmoney.com/{endpoint}"
        assert kwargs["params"]["sort"] == sort

    def test_unknown_pool_type_returns_empty(self, srv):
        """未知 pool_type → 空（不报错，不出去网）。"""
        with patch("asgk_server.capabilities.limitup_pool.egress_request") as m:
            status, payload = srv.handle_capability(
                "limitup_pool", {"pool_type": "bogus", "date": "20260806"})
        assert status == 200
        assert payload["data"] == []
        m.assert_not_called()


# ── 参数构造 ─────────────────────────────────────────────────
class TestParams:
    def test_common_params_present(self, srv):
        """ut/dpt/Pageindex/pagesize/date 与 asgk 一致。"""
        with patch("asgk_server.capabilities.limitup_pool.egress_request",
                   return_value=_resp([])) as m:
            srv.handle_capability("limitup_pool",
                                  {"pool_type": "zt", "date": "20260806"})
        params = m.call_args.kwargs["params"]
        assert params["ut"] == "7eea3edcaed734bea9cbfc24409ed989"
        assert params["dpt"] == "wz.ztzt"
        assert params["Pageindex"] == 0
        assert params["pagesize"] == 10000
        assert params["date"] == "20260806"

    def test_referer_header(self, srv):
        with patch("asgk_server.capabilities.limitup_pool.egress_request",
                   return_value=_resp([])) as m:
            srv.handle_capability("limitup_pool",
                                  {"pool_type": "zt", "date": "20260806"})
        assert m.call_args.kwargs["headers"]["Referer"] == "https://quote.eastmoney.com/"


# ── pool 解析 ────────────────────────────────────────────────
class TestPoolParsing:
    def test_returns_pool_array(self, srv):
        pool = [{"c": "600519", "n": "贵州茅台", "p": 1308500, "zdp": 1.5}]
        with patch("asgk_server.capabilities.limitup_pool.egress_request",
                   return_value=_resp(pool)):
            status, payload = srv.handle_capability(
                "limitup_pool", {"pool_type": "zt", "date": "20260806"})
        assert status == 200
        assert payload["data"] == pool

    def test_data_null_returns_empty(self, srv):
        """非交易日 data=null → []。"""
        with patch("asgk_server.capabilities.limitup_pool.egress_request",
                   return_value=_resp_no_data()):
            status, payload = srv.handle_capability(
                "limitup_pool", {"pool_type": "zt", "date": "20260807"})
        assert status == 200
        assert payload["data"] == []

    def test_pool_missing_returns_empty(self, srv):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {"data": {}}  # 无 pool 键
        with patch("asgk_server.capabilities.limitup_pool.egress_request",
                   return_value=r):
            status, payload = srv.handle_capability(
                "limitup_pool", {"pool_type": "zt", "date": "20260806"})
        assert payload["data"] == []


# ── 熔断 ─────────────────────────────────────────────────────
class TestCircuit:
    def test_403_triggers_circuit(self, srv):
        with patch("asgk_server.capabilities.limitup_pool.egress_request",
                   return_value=_resp_no_data(status=403)):
            status1, _ = srv.handle_capability(
                "limitup_pool", {"pool_type": "zt", "date": "20260806"})
        assert status1 == 403
        with patch("asgk_server.capabilities.limitup_pool.egress_request") as m:
            status2, _ = srv.handle_capability(
                "limitup_pool", {"pool_type": "zt", "date": "20260806"})
        assert status2 == 503
        m.assert_not_called()


# ── realtime no-cache ────────────────────────────────────────
class TestCache:
    def test_realtime_no_cache_each_call_hits_upstream(self, srv):
        """realtime 型 TTL=0：每次都出网。"""
        with patch("asgk_server.capabilities.limitup_pool.egress_request",
                   return_value=_resp([])) as m:
            srv.handle_capability("limitup_pool",
                                  {"pool_type": "zt", "date": "20260806"})
            srv.handle_capability("limitup_pool",
                                  {"pool_type": "zt", "date": "20260806"})
        assert m.call_count == 2
