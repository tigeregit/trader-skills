"""chip 能力服务端测试（mock K 线取数 + 真实 CYQ 引擎）。

验证筹码分布上游知识（push2his K线 + 百度降级链 + cyq.js 计算）正确下沉：
  - push2his 空 → 百度降级取数 → CYQ 计算返回结构化筹码分布
  - push2his 成功直接用 → CYQ 计算
  - 两源都空 → [] 或 None（视 failed 态）
  - CYQ 字段映射（benefitPart/avgCost/percentChips 90/70）

CYQ 引擎用真实的 cyq.js（resources/cyq.js），K 线取数 mock（不打真实上游）。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from asgk_server import registry
from asgk_server.server import CapabilityServer


@pytest.fixture(autouse=True)
def _keep_registry():
    yield


def _config() -> dict:
    return {
        "group": [{"name": "eastmoney", "domains": ["push2his.eastmoney.com"],
                   "rps": 100, "jitter": [0, 0]},
                  {"name": "baidu", "domains": ["finance.pae.baidu.com"],
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


def _kline_records(n: int = 5) -> list[dict]:
    """构造 n 根 K 线（对齐 CYQ 输入格式）。"""
    return [{"index": i, "date": f"2026-08-0{i+1}",
             "open": 10.0 + i, "close": 10.5 + i, "high": 11.0 + i,
             "low": 9.5 + i, "volume": 1000.0, "volume_money": 10000.0,
             "zf": 0.01, "zdf": 0.01, "zde": 0.1, "hsl": 0.5} for i in range(n)]


class TestChipFetch:
    def test_push2his_success_computes_cyq(self, srv):
        """push2his 返回 K 线 → CYQ 计算筹码分布。"""
        recs = _kline_records(5)
        with patch("asgk_server.capabilities.chip._fetch_klines_push2his",
                   return_value=recs):
            status, payload = srv.handle_capability(
                "chip", {"code": "000001", "adjust": ""})
        assert status == 200
        data = payload["data"]
        assert len(data) == 5
        # CYQ 字段映射
        assert "benefit_part" in data[0]
        assert "avg_cost" in data[0]
        assert "pct90_low" in data[0]
        assert "pct70_concentration" in data[0]
        assert data[0]["date"] == "2026-08-01"

    def test_push2his_empty_falls_back_to_baidu(self, srv):
        """push2his 空 → 百度降级取数 → CYQ 计算。"""
        recs = _kline_records(3)
        with patch("asgk_server.capabilities.chip._fetch_klines_push2his",
                   return_value=[]), \
             patch("asgk_server.capabilities.chip._fetch_klines_baidu",
                   return_value=recs) as mock_baidu:
            status, payload = srv.handle_capability(
                "chip", {"code": "000001", "adjust": ""})
        assert status == 200
        assert len(payload["data"]) == 3
        mock_baidu.assert_called_once()

    def test_both_sources_empty_returns_empty(self, srv):
        """两源都空（无 failed）→ 返回空 list。"""
        with patch("asgk_server.capabilities.chip._fetch_klines_push2his",
                   return_value=[]), \
             patch("asgk_server.capabilities.chip._fetch_klines_baidu",
                   return_value=[]):
            status, payload = srv.handle_capability(
                "chip", {"code": "000001", "adjust": ""})
        assert status == 200
        assert payload["data"] == []

    def test_adjust_param_passed_to_push2his(self, srv):
        """adjust 参数（qfq→1, hfq→2, ''→0）正确传到 push2his。"""
        with patch("asgk_server.capabilities.chip._fetch_klines_push2his",
                   return_value=_kline_records(1)) as mock_p2:
            srv.handle_capability("chip", {"code": "000001", "adjust": "qfq"})
        # _fetch_klines_push2his(ctx, code, adjust) 第三参数是 fqt 编码
        assert mock_p2.call_args.args[2] == "1"

    def test_returns_last_90_days(self, srv):
        """K 线 >90 根时只返回最近 90 日。"""
        recs = _kline_records(95)
        with patch("asgk_server.capabilities.chip._fetch_klines_push2his",
                   return_value=recs):
            status, payload = srv.handle_capability(
                "chip", {"code": "000001", "adjust": ""})
        assert status == 200
        assert len(payload["data"]) == 90
