"""fund_flow 能力客户端路由测试（T6）。

验证 eastmoney_fund_flow_minute（signal）与 stock_fund_flow_120d（capital）
优先走 fund_flow 能力，klines CSV 解析在服务端；回退旧 em_get 路径。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from asgk import em_proxy


class TestFundFlowMinuteRouting:
    def test_server_path_returns_structured(self, monkeypatch):
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        server_data = [{"time": "0930", "main_net": -1000, "small_net": 200}]
        from asgk.signal import eastmoney_fund_flow_minute
        with patch("asgk.signal._server_call", return_value=server_data) as sc, \
             patch("asgk.signal.em_get") as gw:
            result = eastmoney_fund_flow_minute("600519")
        assert result == server_data
        sc.assert_called_once_with("fund_flow",
                                   {"code": "600519", "period": "minute"})
        gw.assert_not_called()

    def test_fallback_parses_klines(self, monkeypatch):
        """回退路径：本地 klines CSV 解析。"""
        monkeypatch.setattr(em_proxy, "_SERVER", None)
        from asgk.signal import eastmoney_fund_flow_minute
        resp = MagicMock()
        resp.json.return_value = {"data": {"klines": [
            "0930,-1000,200,300,400,500"]}}
        with patch("asgk.signal.em_get", return_value=resp) as gw:
            result = eastmoney_fund_flow_minute("600519")
        assert result[0]["time"] == "0930"
        assert result[0]["main_net"] == -1000.0
        gw.assert_called_once()

    def test_fallback_when_server_fails(self, monkeypatch):
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        from asgk.signal import eastmoney_fund_flow_minute
        resp = MagicMock()
        resp.json.return_value = {"data": {}}
        with patch("asgk.signal._server_call", return_value=None) as sc, \
             patch("asgk.signal.em_get", return_value=resp) as gw:
            eastmoney_fund_flow_minute("600519")
        sc.assert_called_once()
        gw.assert_called_once()


class TestFundFlow120dRouting:
    def test_server_path_returns_structured(self, monkeypatch):
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        server_data = [{"date": "2026-08-05", "main_net": 1000, "small_net": 0}]
        from asgk.capital import stock_fund_flow_120d
        with patch("asgk.capital._server_call", return_value=server_data) as sc, \
             patch("asgk.capital.em_get") as gw:
            result = stock_fund_flow_120d("600519")
        assert result == server_data
        sc.assert_called_once_with("fund_flow",
                                   {"code": "600519", "period": "daily120"})
        gw.assert_not_called()

    def test_fallback_parses_klines_with_dash(self, monkeypatch):
        """回退路径：日级 '-' 当 0。"""
        monkeypatch.setattr(em_proxy, "_SERVER", None)
        from asgk.capital import stock_fund_flow_120d
        resp = MagicMock()
        resp.json.return_value = {"data": {"klines": [
            "2026-08-05,1000,-,200,300,400,500,extra"]}}
        with patch("asgk.capital.em_get", return_value=resp):
            result = stock_fund_flow_120d("600519")
        assert result[0]["date"] == "2026-08-05"
        assert result[0]["main_net"] == 1000.0
        assert result[0]["small_net"] == 0  # "-" → 0
