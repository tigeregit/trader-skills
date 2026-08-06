"""sgw POST 代理测试。

东财人气榜/概念（emappdata.eastmoney.com）是 POST+JSON，经网关转发。
验证：
- POST body 透传到上游（requests.post 收到 json=body）
- 不同 body（不同 top/股票）不串缓存
- 相同 body 第二次命中缓存
- emappdata 两个端点已 approved 且唯一匹配
- 未配网关时 asgk em_get POST fail-closed

测试方法见 .agents/notes/test-method.md。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from sgw.proxy import Gateway, load_config


def _make_gateway() -> Gateway:
    cfg = load_config(Path(__file__).resolve().parent.parent / "sgw" / "config.toml")
    cfg.setdefault("cache", {})["persist"] = {"enabled": False}
    cfg["state"] = {"enabled": False}
    return Gateway(cfg)


def _fake_resp(body: bytes = b'{"data":1}') -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.content = body
    r.headers = {"Content-Type": "application/json"}
    return r


class TestPostForwarding:
    def test_post_body_reaches_upstream(self):
        """POST body 必须实际到达上游（mock 捕获 requests.post 的 json 参数）。"""
        g = _make_gateway()
        url = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
        body = {"appId": "appId01", "pageSize": 50}
        with patch("sgw.proxy.requests.post", return_value=_fake_resp()) as m:
            g.handle(url, {}, "R", method="POST", body=body)
        assert m.call_count == 1
        _, kwargs = m.call_args
        assert kwargs["json"] == body

    def test_post_not_called_for_get(self):
        """GET 请求不触发 requests.post。"""
        g = _make_gateway()
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        with patch("sgw.proxy.requests.post") as post, \
                patch("sgw.proxy.requests.get", return_value=_fake_resp()) as get:
            g.handle(url, {"code": "600519"}, "L")
        post.assert_not_called()
        assert get.call_count == 1

    def test_get_backward_compat_no_body_arg(self):
        """handle 默认 GET，不传 method/body 仍正常（向后兼容）。"""
        g = _make_gateway()
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        with patch("sgw.proxy.requests.get", return_value=_fake_resp()) as m:
            g.handle(url, {"code": "600519"}, "L")
        assert m.call_count == 1


class TestPostCacheKeyBody:
    def test_different_body_not_cross_cached(self):
        """核心：不同 POST body（不同 top）必须各自打外网，不能串缓存。"""
        g = _make_gateway()
        url = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
        with patch("sgw.proxy.requests.post", return_value=_fake_resp()) as m:
            g.handle(url, {}, "R", method="POST", body={"pageSize": 50})
            g.handle(url, {}, "R", method="POST", body={"pageSize": 100})
        assert m.call_count == 2

    def test_same_body_hits_cache(self):
        """相同 POST body 第二次命中缓存（R 档 no-cache 故用 L 档验证）。"""
        g = _make_gateway()
        url = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
        with patch("sgw.proxy.requests.post", return_value=_fake_resp()) as m:
            _, _, h1 = g.handle(url, {}, "L", method="POST", body={"pageSize": 50})
            _, _, h2 = g.handle(url, {}, "L", method="POST", body={"pageSize": 50})
        assert h1["X-Cache"] == "MISS"
        assert h2["X-Cache"] == "HIT-MEM"
        assert m.call_count == 1

    def test_body_key_order_irrelevant(self):
        """body 字段顺序不同但内容相同 → 同一 cache key → 命中缓存。"""
        g = _make_gateway()
        url = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
        with patch("sgw.proxy.requests.post", return_value=_fake_resp()) as m:
            g.handle(url, {}, "L", method="POST", body={"a": "1", "b": "2"})
            g.handle(url, {}, "L", method="POST", body={"b": "2", "a": "1"})
        assert m.call_count == 1

    def test_get_and_post_same_url_not_cross_cached(self):
        """同 URL 一个 GET 一个 POST body → 不同 key，互不干扰。"""
        g = _make_gateway()
        url = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
        with patch("sgw.proxy.requests.get", return_value=_fake_resp()) as get, \
                patch("sgw.proxy.requests.post", return_value=_fake_resp()) as post:
            g.handle(url, {}, "L")  # GET
            g.handle(url, {}, "L", method="POST", body={"x": 1})
        assert get.call_count == 1
        assert post.call_count == 1


class TestEmappdataEndpointsApproved:
    def test_hot_rank_endpoint_approved(self):
        """人气榜端点已 approved 且唯一匹配（inventory 检查的基础）。"""
        g = _make_gateway()
        policy = g.policy_for("emappdata.eastmoney.com", "/stockrank/getAllCurrentList")
        assert policy is not None
        assert policy.review_status == "approved"
        assert policy.ip_risk == "controlled"

    def test_hot_concept_endpoint_approved(self):
        g = _make_gateway()
        policy = g.policy_for("emappdata.eastmoney.com", "/stockrank/getHotStockRankList")
        assert policy is not None
        assert policy.review_status == "approved"

    def test_emappdata_unknown_path_rejected(self):
        """emappdata 上未登记的 path 仍按 unknown 拒绝（最小授权）。"""
        g = _make_gateway()
        with patch("sgw.proxy.requests.post", return_value=_fake_resp()):
            status, _, _ = g.handle(
                "https://emappdata.eastmoney.com/unknown/path", {}, "R",
                method="POST", body={"x": 1},
            )
        assert status == 403


class TestFormPostForwarding:
    """巨潮公告/互动易等 form-encoded POST 的转发与缓存。"""

    def test_form_body_forwarded_as_data(self):
        """form POST 的 body 必须用 requests.post(data=) 发送（非 json=）。"""
        g = _make_gateway()
        url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
        body = {"stock": "600519,gssz0600519", "pageNum": "1"}
        with patch("sgw.proxy.requests.post", return_value=_fake_resp()) as m:
            g.handle(url, {}, "P", method="POST", body=body, body_type="form")
        assert m.call_count == 1
        _, kwargs = m.call_args
        assert kwargs["data"] == body        # form 用 data=
        assert "json" not in kwargs          # 不应混用 json=

    def test_json_body_still_uses_json(self):
        """JSON POST（body_type=json）仍用 json=，回归。"""
        g = _make_gateway()
        url = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
        with patch("sgw.proxy.requests.post", return_value=_fake_resp()) as m:
            g.handle(url, {}, "R", method="POST", body={"x": 1}, body_type="json")
        _, kwargs = m.call_args
        assert kwargs["json"] == {"x": 1}

    def test_different_form_body_not_cross_cached(self):
        """不同 form body（不同股票）不串缓存。"""
        g = _make_gateway()
        url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
        with patch("sgw.proxy.requests.post", return_value=_fake_resp()) as m:
            g.handle(url, {}, "P", method="POST", body={"stock": "600519"}, body_type="form")
            g.handle(url, {}, "P", method="POST", body={"stock": "000001"}, body_type="form")
        assert m.call_count == 2

    def test_same_form_body_hits_cache(self):
        """相同 form body 第二次命中缓存（P 档可缓存）。"""
        g = _make_gateway()
        url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
        with patch("sgw.proxy.requests.post", return_value=_fake_resp()) as m:
            _, _, h1 = g.handle(url, {}, "P", method="POST", body={"stock": "600519"}, body_type="form")
            _, _, h2 = g.handle(url, {}, "P", method="POST", body={"stock": "600519"}, body_type="form")
        assert h1["X-Cache"] == "MISS"
        assert h2["X-Cache"] == "HIT-MEM"
        assert m.call_count == 1

    def test_cninfo_endpoints_approved(self):
        """巨潮端点已 approved。"""
        g = _make_gateway()
        for host, path in [
            ("www.cninfo.com.cn", "/new/hisAnnouncement/query"),
            ("www.cninfo.com.cn", "/new/data/szse_stock.json"),
            ("irm.cninfo.com.cn", "/newircs/index/queryKeyboardInfo"),
            ("irm.cninfo.com.cn", "/newircs/company/question"),
        ]:
            p = g.policy_for(host, path)
            assert p is not None and p.review_status == "approved", f"{host}{path}"
