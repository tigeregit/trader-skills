"""sgw egress client 选择测试。

验证 endpoint 的 egress_client 字段：
- 默认 requests 出网（绝大多数源）
- curl_cffi 出网（百度等有 TLS 指纹风控的源，带 impersonate=chrome）
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


class TestEgressClientSelection:
    def test_default_uses_requests(self):
        """未标 egress_client 的端点用 requests 出网。"""
        g = _make_gateway()
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        with patch("sgw.proxy.requests.get", return_value=_fake_resp()) as m:
            g.handle(url, {"code": "600519"}, "L")
        assert m.call_count == 1

    def test_baidu_uses_curl_cffi(self):
        """标 egress_client=curl_cffi 的端点用 curl_cffi 出网（带 impersonate=chrome）。"""
        g = _make_gateway()
        url = "https://finance.pae.baidu.com/selfselect/getstockquotation"
        with patch("sgw.proxy.requests.get") as req_get, \
                patch("curl_cffi.requests.request", return_value=_fake_resp()) as curl_req:
            g.handle(url, {"code": "600519"}, "R")
        req_get.assert_not_called()
        assert curl_req.call_count == 1
        _, kwargs = curl_req.call_args
        assert kwargs["impersonate"] == "chrome"

    def test_curl_cffi_requests_error_caught(self):
        """curl_cffi 的 RequestsError 被 _fetch_upstream 的 except 捕获（不崩）。"""
        import requests as _req
        from curl_cffi.requests import RequestsError
        g = _make_gateway()
        url = "https://finance.pae.baidu.com/selfselect/getstockquotation"
        err = RequestsError("simulated curl failure")
        with patch("curl_cffi.requests.request", side_effect=err):
            status, body, _ = g.handle(url, {"code": "600519"}, "R")
        # 失败 → 502，不崩
        assert status in (502, 503)

    def test_baidu_endpoint_marked_curl_cffi(self):
        """百度端点策略的 egress_client 字段为 curl_cffi。"""
        g = _make_gateway()
        policy = g.policy_for("finance.pae.baidu.com", "/selfselect/getstockquotation")
        assert policy is not None
        assert policy.egress_client == "curl_cffi"

    def test_eastmoney_endpoint_default_requests(self):
        """东财端点未标 egress_client，默认 requests。"""
        g = _make_gateway()
        policy = g.policy_for("datacenter-web.eastmoney.com", "/api/data/v1/get")
        assert policy.egress_client == "requests"
