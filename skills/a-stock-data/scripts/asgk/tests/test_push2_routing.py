"""push2 能力（stock_info / concept_blocks）客户端路由测试（T6）。

验证 eastmoney_stock_info / eastmoney_concept_blocks 优先走能力代理服务端，
字段映射在服务端，客户端零上游知识；回退旧 em_get 路径。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from asgk import em_proxy


# ── stock_info 路由 ───────────────────────────────────────────
class TestStockInfoRouting:
    def test_server_path_returns_structured(self, monkeypatch):
        """配了 ASGK_SERVER → 走 stock_info 能力，字段映射在服务端。"""
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        server_data = {"code": "600519", "name": "贵州茅台", "price": 1308.55}
        from asgk.base import eastmoney_stock_info
        with patch("asgk.base._server_call", return_value=server_data) as sc, \
             patch("asgk.base.em_get") as gw:
            result = eastmoney_stock_info("600519")
        assert result == server_data
        sc.assert_called_once_with("stock_info", {"code": "600519"})
        gw.assert_not_called()

    def test_fallback_when_server_unset(self, monkeypatch):
        """未配服务端 → 回退旧 em_get 路径，本地字段映射。"""
        monkeypatch.setattr(em_proxy, "_SERVER", None)
        from asgk.base import eastmoney_stock_info
        resp = MagicMock()
        resp.json.return_value = {"data": {"f57": "600519", "f58": "贵州茅台",
                                           "f43": 1308}}
        with patch("asgk.base.em_get", return_value=resp) as gw:
            result = eastmoney_stock_info("600519")
        assert result["code"] == "600519"
        assert result["name"] == "贵州茅台"
        assert result["price"] == 1308
        gw.assert_called_once()

    def test_fallback_when_server_fails(self, monkeypatch):
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        from asgk.base import eastmoney_stock_info
        resp = MagicMock()
        resp.json.return_value = {"data": {}}
        with patch("asgk.base._server_call", return_value=None) as sc, \
             patch("asgk.base.em_get", return_value=resp) as gw:
            eastmoney_stock_info("600519")
        sc.assert_called_once()
        gw.assert_called_once()


# ── concept_blocks 路由 ───────────────────────────────────────
class TestConceptBlocksRouting:
    def test_server_path_returns_structured(self, monkeypatch):
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        server_data = {"total": 2, "boards": [{"name": "白酒", "code": "BK0477"}],
                       "concept_tags": ["白酒"]}
        from asgk.signal import eastmoney_concept_blocks
        with patch("asgk.signal._server_call", return_value=server_data) as sc, \
             patch("asgk.signal.em_get") as gw:
            result = eastmoney_concept_blocks("600519")
        assert result == server_data
        sc.assert_called_once_with("concept_blocks", {"code": "600519"})
        gw.assert_not_called()

    def test_fallback_legacy_parses_diff(self, monkeypatch):
        """回退路径：本地 diff 解析（f14→name 等）。"""
        monkeypatch.setattr(em_proxy, "_SERVER", None)
        from asgk.signal import eastmoney_concept_blocks
        resp = MagicMock()
        resp.json.return_value = {"data": {"diff": {
            "1": {"f12": "BK1", "f14": "白酒", "f3": 1.5, "f128": "贵州茅台"}}}}
        with patch("asgk.signal.em_get", return_value=resp):
            result = eastmoney_concept_blocks("600519")
        assert result["total"] == 1
        assert result["boards"][0]["name"] == "白酒"
        assert result["concept_tags"] == ["白酒"]

    def test_fallback_when_server_fails(self, monkeypatch):
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        from asgk.signal import eastmoney_concept_blocks
        resp = MagicMock()
        resp.json.return_value = {"data": {}}
        with patch("asgk.signal._server_call", return_value=None) as sc, \
             patch("asgk.signal.em_get", return_value=resp) as gw:
            eastmoney_concept_blocks("600519")
        sc.assert_called_once()
        gw.assert_called_once()
