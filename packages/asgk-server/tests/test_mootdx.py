"""mootdx 能力服务端测试（mock TCP 客户端池）。

验证通达信 TCP 上游知识（探测兜底链、客户端池、DataFrame→records 转换、
日线百度降级）正确下沉到服务端。不打真实 TCP——mock
asgk_server.capabilities.mootdx._get_client 返回构造的 fake client。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from asgk_server import registry
from asgk_server.server import CapabilityServer


@pytest.fixture(autouse=True)
def _keep_registry():
    """mootdx 能力在模块导入时注册，不要 clear。"""
    yield


def _config() -> dict:
    return {
        "group": [{"name": "mootdx", "domains": [], "rps": 100, "jitter": [0, 0]}],
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


def _fake_client(bars_df=None, quotes_df=None, transaction_df=None,
                 finance_df=None, f10=None) -> MagicMock:
    """构造 fake mootdx client（methods 返回指定 DataFrame/dict/str）。"""
    c = MagicMock()
    c.bars.return_value = bars_df if bars_df is not None else pd.DataFrame()
    c.quotes.return_value = quotes_df if quotes_df is not None else pd.DataFrame()
    c.transaction.return_value = (transaction_df
                                  if transaction_df is not None else pd.DataFrame())
    c.finance.return_value = finance_df if finance_df is not None else pd.DataFrame()
    c.F10.return_value = f10 if f10 is not None else {}
    return c


class TestMootdxBars:
    def test_bars_returns_records(self, srv):
        """bars：mootdx DataFrame → list[dict]。"""
        df = pd.DataFrame([
            {"open": 10.0, "close": 10.5, "datetime": "2026-08-06"},
        ])
        client = _fake_client(bars_df=df)
        with patch("asgk_server.capabilities.mootdx._get_client", return_value=client):
            status, payload = srv.handle_capability(
                "mootdx", {"mootdx_type": "bars", "code": "600519",
                           "frequency": 9, "offset": 100})
        assert status == 200
        data = payload["data"]
        assert len(data) == 1
        assert data[0]["close"] == 10.5

    def test_bars_daily_empty_falls_back_to_baidu(self, srv):
        """日线(9)空响应 → 百度降级（_baidu_kline_fallback 调本服务端 baidu_kline）。"""
        client = _fake_client(bars_df=pd.DataFrame())  # 空日 K
        # keys 与 rows 下标对齐（time=日期，volume→vol，amount 不变）
        baidu_data = {"keys": ["time", "open", "close", "high", "low",
                               "volume", "amount"],
                      "rows": ["2026-08-06,10.0,10.5,11.0,9.5,1000,10000"]}
        with patch("asgk_server.capabilities.mootdx._get_client", return_value=client), \
             patch("asgk_server.capabilities.mootdx._baidu_kline_fallback",
                   return_value=baidu_data) as mock_fallback:
            status, payload = srv.handle_capability(
                "mootdx", {"mootdx_type": "bars", "code": "600519",
                           "frequency": 9, "offset": 100})
        assert status == 200
        data = payload["data"]
        assert len(data) == 1
        assert data[0]["close"] == 10.5
        assert data[0]["datetime"] == "2026-08-06"
        mock_fallback.assert_called_once()

    def test_bars_minute_empty_no_fallback(self, srv):
        """分钟(0)空响应不降级（不可等价映射），返回空 list。"""
        client = _fake_client(bars_df=pd.DataFrame())
        with patch("asgk_server.capabilities.mootdx._get_client", return_value=client), \
             patch("asgk_server.capabilities.mootdx._baidu_kline_fallback") as mock_fallback:
            status, payload = srv.handle_capability(
                "mootdx", {"mootdx_type": "bars", "code": "600519",
                           "frequency": 0, "offset": 100})
        assert status == 200
        assert payload["data"] == []
        mock_fallback.assert_not_called()


class TestMootdxVariants:
    def test_quotes(self, srv):
        df = pd.DataFrame([{"price": 1820.0, "name": "贵州茅台"}])
        client = _fake_client(quotes_df=df)
        with patch("asgk_server.capabilities.mootdx._get_client", return_value=client):
            status, payload = srv.handle_capability(
                "mootdx", {"mootdx_type": "quotes", "symbols": ["600519"]})
        assert status == 200
        assert payload["data"][0]["price"] == 1820.0

    def test_transaction(self, srv):
        df = pd.DataFrame([{"time": "09:30", "price": 10.0, "vol": 100}])
        client = _fake_client(transaction_df=df)
        with patch("asgk_server.capabilities.mootdx._get_client", return_value=client):
            status, payload = srv.handle_capability(
                "mootdx", {"mootdx_type": "transaction", "code": "600519"})
        assert status == 200
        assert len(payload["data"]) == 1

    def test_finance(self, srv):
        df = pd.DataFrame([{"liutongguben": 1.25e9, "industry": "白酒"}])
        client = _fake_client(finance_df=df)
        with patch("asgk_server.capabilities.mootdx._get_client", return_value=client):
            status, payload = srv.handle_capability(
                "mootdx", {"mootdx_type": "finance", "code": "600519"})
        assert status == 200
        assert payload["data"]["liutongguben"] == 1.25e9

    def test_finance_empty_returns_empty_dict(self, srv):
        client = _fake_client(finance_df=pd.DataFrame())
        with patch("asgk_server.capabilities.mootdx._get_client", return_value=client):
            status, payload = srv.handle_capability(
                "mootdx", {"mootdx_type": "finance", "code": "600519"})
        assert status == 200
        assert payload["data"] == {}

    def test_f10(self, srv):
        client = _fake_client(f10={"最新提示": "贵州茅台..."})
        with patch("asgk_server.capabilities.mootdx._get_client", return_value=client):
            status, payload = srv.handle_capability(
                "mootdx", {"mootdx_type": "f10", "code": "600519", "name": "公司概况"})
        assert status == 200
        assert payload["data"] == {"最新提示": "贵州茅台..."}


class TestMootdxClientPool:
    def test_network_error_resets_client(self, srv):
        """TCP OSError（连接断）→ 网络错误反馈 + 清池重置。"""
        client = MagicMock()
        client.bars.side_effect = OSError("connection reset")
        with patch("asgk_server.capabilities.mootdx._get_client", return_value=client), \
             patch("asgk_server.capabilities.mootdx._reset_client") as mock_reset:
            status, payload = srv.handle_capability(
                "mootdx", {"mootdx_type": "bars", "code": "600519"})
        assert status == 502
        mock_reset.assert_called_once()
