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


# ── emquery 路由（§3.4 em_get 枢纽，T6）──────────────────────
class TestEmQueryRouting:
    """em_get 配了 ASGK_SERVER 时优先走 emquery 能力，否则回退 sgw。"""

    def test_server_path_used_when_configured(self, monkeypatch):
        """配了 ASGK_SERVER → 走 emquery，返回伪装 Response。"""
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        em_data = {"data": {"f57": "600519", "f43": 1308}}
        with patch("asgk.em_proxy._server_call", return_value=em_data) as sc, \
             patch("asgk.em_proxy.requests.get") as gw_get:
            r = em_proxy.em_get("https://push2.eastmoney.com/api/qt/stock/get",
                                params={"secid": "1.600519"}, tier="S")
        assert r.json() == em_data  # 伪装 Response.json() 返回服务端数据
        sc.assert_called_once()
        call_params = sc.call_args.args[1]
        assert call_params["url"] == "https://push2.eastmoney.com/api/qt/stock/get"
        assert call_params["method"] == "GET"
        gw_get.assert_not_called()  # emquery 命中，不走 sgw

    def test_fallback_to_sgw_when_server_unset(self, monkeypatch):
        """未配 ASGK_SERVER → _server_call 返回 None → 走 sgw。"""
        monkeypatch.setattr(em_proxy, "_SERVER", None)
        monkeypatch.setattr(em_proxy, "_GW", "http://gw:7700")
        with patch("asgk.em_proxy.requests.get") as gw_get:
            em_proxy.em_get("https://push2.eastmoney.com/api/qt/stock/get", tier="S")
        gw_get.assert_called_once()

    def test_fallback_to_sgw_when_emquery_fails(self, monkeypatch):
        """配了服务端但 emquery 失败（None）→ 回退 sgw。"""
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        monkeypatch.setattr(em_proxy, "_GW", "http://gw:7700")
        with patch("asgk.em_proxy._server_call", return_value=None) as sc, \
             patch("asgk.em_proxy.requests.get") as gw_get:
            em_proxy.em_get("https://push2.eastmoney.com/api/qt/stock/get", tier="S")
        sc.assert_called_once()
        gw_get.assert_called_once()

    def test_post_json_via_emquery(self, monkeypatch):
        """POST+JSON 经 emquery 传给服务端。"""
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        with patch("asgk.em_proxy._server_call", return_value={"ok": True}) as sc:
            em_proxy.em_get("https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
                            method="POST", json={"pageSize": 50}, tier="R")
        call_params = sc.call_args.args[1]
        assert call_params["method"] == "POST"
        assert call_params["body"] == {"pageSize": 50}
        assert call_params["body_type"] == "json"

    def test_post_form_via_emquery(self, monkeypatch):
        """POST+form 经 emquery 传 body_type=form。"""
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        with patch("asgk.em_proxy._server_call", return_value={"ok": True}) as sc:
            em_proxy.em_get("https://www.cninfo.com.cn/new/hisAnnouncement/query",
                            method="POST", data={"stock": "600519"}, tier="P")
        call_params = sc.call_args.args[1]
        assert call_params["body_type"] == "form"
        assert call_params["body"] == {"stock": "600519"}

    def test_xlsx_source_bypasses_emquery(self, monkeypatch):
        """依赖 .content 取 xlsx 的源（ShowReport）bypass emquery，走 sgw。"""
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        monkeypatch.setattr(em_proxy, "_GW", "http://gw:7700")
        with patch("asgk.em_proxy._server_call") as sc, \
             patch("asgk.em_proxy.requests.get") as gw_get:
            em_proxy.em_get("https://www.szse.cn/api/report/ShowReport",
                            params={"SHOWTYPE": "xlsx"}, tier="S")
        sc.assert_not_called()  # bypass emquery
        gw_get.assert_called_once()  # 走 sgw

    def test_emquery_response_mimics_requests(self, monkeypatch):
        """_EmQueryResponse 兼容 .json()/.content/.text/.status_code 接口。"""
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        em_data = {"data": {"f57": "600519"}}
        with patch("asgk.em_proxy._server_call", return_value=em_data):
            r = em_proxy.em_get("https://push2.eastmoney.com/api/qt/stock/get", tier="S")
        assert r.status_code == 200
        assert r.json() == em_data
        assert b"600519" in r.content
        assert "600519" in r.text
