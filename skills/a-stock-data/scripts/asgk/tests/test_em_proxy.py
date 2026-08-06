"""风控源请求入口的失败关闭行为与网关路由。"""
from unittest.mock import MagicMock, patch

import pytest

from asgk import em_proxy


def test_missing_gateway_never_falls_back_to_direct(monkeypatch):
    monkeypatch.setattr(em_proxy, "_GW", None)
    # 旧版本的逃生开关即使残留在部署环境也必须失效。
    monkeypatch.setenv("ASGK_ALLOW_DIRECT", "1")
    with patch("asgk.em_proxy.requests.get") as direct:
        with pytest.raises(RuntimeError, match="禁止直连"):
            em_proxy.em_get("https://push2.eastmoney.com/api/qt/stock/get")
    direct.assert_not_called()


def test_missing_gateway_post_never_falls_back_to_direct(monkeypatch):
    """POST 风控源同样禁止直连（emappdata 人气榜/概念）。"""
    monkeypatch.setattr(em_proxy, "_GW", None)
    with patch("asgk.em_proxy.requests.post") as direct:
        with pytest.raises(RuntimeError, match="禁止直连"):
            em_proxy.em_get(
                "https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
                method="POST", json={"pageSize": 50}, tier="R",
            )
    direct.assert_not_called()


def test_post_routes_through_gateway(monkeypatch):
    """配置网关后，POST+JSON 必须经网关（body 放请求体，?u=url 随 query）。"""
    monkeypatch.setattr(em_proxy, "_GW", "http://gw:7700")
    fake = MagicMock()
    with patch("asgk.em_proxy.requests.post", return_value=fake) as post:
        em_proxy.em_get(
            "https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
            method="POST", json={"pageSize": 50}, tier="R",
        )
    assert post.call_count == 1
    args, kwargs = post.call_args
    assert args[0] == "http://gw:7700"
    # ?u= 指向真实上游
    assert kwargs["params"]["u"] == "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
    # body 放请求体
    assert kwargs["json"] == {"pageSize": 50}
    # tier 进头
    assert kwargs["headers"]["X-Cache-Tier"] == "R"


def test_get_still_uses_get_method(monkeypatch):
    """默认 GET 走网关（回归：加 POST 支持不破坏现有 GET 路径）。"""
    monkeypatch.setattr(em_proxy, "_GW", "http://gw:7700")
    with patch("asgk.em_proxy.requests.get") as get, \
            patch("asgk.em_proxy.requests.post") as post:
        em_proxy.em_get("https://push2.eastmoney.com/api/qt/stock/get", tier="R")
    assert get.call_count == 1
    post.assert_not_called()
