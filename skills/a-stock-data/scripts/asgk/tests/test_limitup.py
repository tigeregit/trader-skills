"""涨停池端点白名单回归测试 + 能力代理服务端路由测试（T6）。"""
from unittest.mock import MagicMock, patch

import pytest

from asgk import em_proxy
from asgk.limitup import _em_zt_api


def test_known_pool_uses_classified_url():
    response = MagicMock()
    response.json.return_value = {"data": None}
    with patch("asgk.limitup.em_get", return_value=response) as em_get:
        assert _em_zt_api("getTopicZTPool", "fbt:asc", "20260731") == []
    assert em_get.call_args.args == (
        "https://push2ex.eastmoney.com/getTopicZTPool",
    )


def test_unknown_pool_fails_before_gateway_call():
    with patch("asgk.limitup.em_get") as em_get:
        with pytest.raises(ValueError, match="未分类"):
            _em_zt_api("unreviewedPool", "fbt:asc", "20260731")
    em_get.assert_not_called()


# ── 能力代理服务端路由（§3.4 渐进迁移，T6）────────────────────
class TestServerRouting:
    """_em_zt_api 优先走 limitup_pool 能力，回退旧 em_get 网关路径。"""

    def test_server_path_used_when_configured(self, monkeypatch):
        """配了 ASGK_SERVER → 走 limitup_pool 能力，返回服务端数据。"""
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        server_pool = [{"c": "600519", "n": "贵州茅台", "p": 1308500}]
        with patch("asgk.limitup._server_call", return_value=server_pool) as sc, \
             patch("asgk.limitup.em_get") as gw:
            result = _em_zt_api("getTopicZTPool", "fbt:asc", "20260806")
        assert result == server_pool
        sc.assert_called_once_with("limitup_pool",
                                   {"pool_type": "zt", "date": "20260806"})
        gw.assert_not_called()  # 服务端命中，不走旧网关

    def test_fallback_when_server_unset(self, monkeypatch):
        """未配 ASGK_SERVER → _server_call 返回 None → 回退旧 em_get 路径。"""
        monkeypatch.setattr(em_proxy, "_SERVER", None)
        response = MagicMock()
        response.json.return_value = {"data": None}
        with patch("asgk.limitup.em_get", return_value=response) as gw:
            result = _em_zt_api("getTopicZTPool", "fbt:asc", "20260806")
        assert result == []
        gw.assert_called_once()

    def test_fallback_when_server_fails(self, monkeypatch):
        """配了服务端但失败 → 回退旧路径。"""
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        response = MagicMock()
        response.json.return_value = {"data": None}
        with patch("asgk.limitup._server_call", return_value=None) as sc, \
             patch("asgk.limitup.em_get", return_value=response) as gw:
            _em_zt_api("getTopicZTPool", "fbt:asc", "20260806")
        sc.assert_called_once()
        gw.assert_called_once()

    def test_all_pool_types_routed(self, monkeypatch):
        """四种 pool_type 都正确映射到服务端参数。"""
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        for endpoint, expected_type in [
            ("getTopicZTPool", "zt"), ("getTopicZBPool", "zb"),
            ("getTopicDTPool", "dt"), ("getYesterdayZTPool", "yzt"),
        ]:
            with patch("asgk.limitup._server_call", return_value=[]) as sc:
                _em_zt_api(endpoint, "sort", "20260806")
            assert sc.call_args.args[1]["pool_type"] == expected_type
