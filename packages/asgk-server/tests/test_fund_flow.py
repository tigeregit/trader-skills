"""fund_flow 能力服务端测试。

验证东财资金流（minute + daily120）的上游知识下沉：
  - period 参数驱动端点选择（minute→push2/kline, daily120→push2his/daykline）
  - secid 市场前缀
  - klines CSV split 解析（字段索引下沉服务端）
  - dash_zero：日级 "-" 当 0
  - 403 触发熔断
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from asgk_server.server import CapabilityServer


def _config() -> dict:
    return {
        "group": [{"name": "eastmoney",
                   "domains": ["push2.eastmoney.com", "push2his.eastmoney.com"],
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


def _resp(klines: list[str], status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"data": {"klines": klines}}
    return r


# ── period 路由 ──────────────────────────────────────────────
class TestPeriodRouting:
    def test_minute_uses_push2_kline_endpoint(self, srv):
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp([])) as m:
            srv.handle_capability("fund_flow",
                                  {"code": "600519", "period": "minute"})
        url = m.call_args.args[2]
        assert url == "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
        assert m.call_args.kwargs["params"]["klt"] == 1

    def test_daily120_uses_push2his_daykline_endpoint(self, srv):
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp([])) as m:
            srv.handle_capability("fund_flow",
                                  {"code": "600519", "period": "daily120"})
        url = m.call_args.args[2]
        assert url == "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
        assert m.call_args.kwargs["params"]["lmt"] == "120"

    def test_unknown_period_returns_empty(self, srv):
        with patch("asgk_server.capabilities.push2.egress_request") as m:
            status, payload = srv.handle_capability(
                "fund_flow", {"code": "600519", "period": "bogus"})
        assert status == 200
        assert payload["data"] == []
        m.assert_not_called()


# ── secid ────────────────────────────────────────────────────
class TestSecid:
    def test_secid_sh(self, srv):
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp([])) as m:
            srv.handle_capability("fund_flow",
                                  {"code": "600519", "period": "minute"})
        assert m.call_args.kwargs["params"]["secid"] == "1.600519"

    def test_secid_sz(self, srv):
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp([])) as m:
            srv.handle_capability("fund_flow",
                                  {"code": "000001", "period": "minute"})
        assert m.call_args.kwargs["params"]["secid"] == "0.000001"


# ── klines 解析 ──────────────────────────────────────────────
class TestKlinesParse:
    def test_minute_parse(self, srv):
        """分钟级：6 字段，key=time。"""
        klines = ["0930,-1000,200,300,400,500", "0931,2000,100,200,300,400"]
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp(klines)):
            status, payload = srv.handle_capability(
                "fund_flow", {"code": "600519", "period": "minute"})
        rows = payload["data"]
        assert len(rows) == 2
        assert rows[0] == {"time": "0930", "main_net": -1000.0,
                           "small_net": 200.0, "mid_net": 300.0,
                           "large_net": 400.0, "super_net": 500.0}

    def test_daily120_parse_with_dash(self, srv):
        """日级：7+ 字段，key=date，"-" 当 0。"""
        klines = ["2026-08-05,1000,-,200,300,400,500,extra"]
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp(klines)):
            status, payload = srv.handle_capability(
                "fund_flow", {"code": "600519", "period": "daily120"})
        rows = payload["data"]
        assert rows[0]["date"] == "2026-08-05"
        assert rows[0]["main_net"] == 1000.0
        assert rows[0]["small_net"] == 0  # "-" → 0

    def test_empty_klines(self, srv):
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp([])):
            status, payload = srv.handle_capability(
                "fund_flow", {"code": "600519", "period": "minute"})
        assert payload["data"] == []

    def test_short_line_skipped(self, srv):
        """不足 min_parts 的行跳过。"""
        klines = ["0930,100", "0931,1000,200,300,400,500"]
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp(klines)):
            status, payload = srv.handle_capability(
                "fund_flow", {"code": "600519", "period": "minute"})
        assert len(payload["data"]) == 1  # 短行跳过


# ── 熔断 ─────────────────────────────────────────────────────
class TestCircuit:
    def test_403_triggers_circuit(self, srv):
        with patch("asgk_server.capabilities.push2.egress_request",
                   return_value=_resp([], status=403)):
            status1, _ = srv.handle_capability(
                "fund_flow", {"code": "600519", "period": "minute"})
        assert status1 == 403
        with patch("asgk_server.capabilities.push2.egress_request") as m:
            status2, _ = srv.handle_capability(
                "fund_flow", {"code": "600519", "period": "minute"})
        assert status2 == 503
        m.assert_not_called()
