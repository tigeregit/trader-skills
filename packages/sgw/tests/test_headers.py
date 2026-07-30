"""sgw 请求头白名单透传测试。

验证：
- 白名单内 header（Referer/Cookie/X-CSRF-Token）透传到上游
- 非白名单 header（Host/Authorization）被丢弃
- 不同 Referer 产生不同 cache key（不串缓存）
- _filtered_client_headers 大小写不敏感匹配
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from sgw.proxy import (
    Gateway, _filtered_client_headers, UPSTREAM_HEADER_ALLOWLIST,
    load_config,
)
from pathlib import Path


def _make_gateway() -> Gateway:
    cfg = load_config(Path(__file__).resolve().parent.parent / "sgw" / "config.toml")
    cfg.setdefault("cache", {})["persist"] = {"enabled": False}
    return Gateway(cfg)


def _fake_resp(body: bytes = b'{"data":1}') -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.content = body
    r.headers = {"Content-Type": "application/json"}
    return r


# ── _filtered_client_headers 单元 ─────────────────────────
class TestFilteredHeaders:
    def test_none_returns_default_ua(self):
        h = _filtered_client_headers(None)
        assert h == {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    def test_whitelist_passed_through(self):
        h = _filtered_client_headers({"Referer": "https://www.szse.cn/x"})
        assert h["Referer"] == "https://www.szse.cn/x"

    def test_non_whitelist_dropped(self):
        """Host/Authorization 等敏感头禁止透传。"""
        h = _filtered_client_headers({
            "Host": "evil.com", "Authorization": "Bearer x", "Content-Length": "5",
            "Connection": "keep-alive",
        })
        assert "Host" not in h
        assert "Authorization" not in h
        assert "Content-Length" not in h
        assert "Connection" not in h

    def test_case_insensitive_match(self):
        """HTTP 头名大小写无关，必须规范化匹配。"""
        h = _filtered_client_headers({"referer": "https://x", "COOKIE": "s=1"})
        assert h["Referer"] == "https://x"
        assert h["Cookie"] == "s=1"

    def test_empty_value_dropped(self):
        h = _filtered_client_headers({"Referer": ""})
        assert "Referer" not in h

    def test_default_ua_when_missing(self):
        """客户端未传 UA 时填默认。"""
        h = _filtered_client_headers({"Referer": "https://x"})
        assert "User-Agent" in h

    def test_client_ua_overrides_default(self):
        h = _filtered_client_headers({"User-Agent": "MyBot/1.0"})
        assert h["User-Agent"] == "MyBot/1.0"


# ── Gateway handle 透传集成 ───────────────────────────────
class TestGatewayHeaderForwarding:
    def test_referer_reaches_upstream(self):
        """Referer 必须实际到达上游（mock 捕获 requests.get 的 headers 参数）。"""
        g = _make_gateway()
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        with patch("sgw.proxy.requests.get", return_value=_fake_resp()) as m:
            g.handle(url, {}, "L", client_headers={"Referer": "https://www.szse.cn/x"})
        assert m.call_count == 1
        _, kwargs = m.call_args
        assert kwargs["headers"]["Referer"] == "https://www.szse.cn/x"

    def test_host_not_forwarded(self):
        g = _make_gateway()
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        with patch("sgw.proxy.requests.get", return_value=_fake_resp()) as m:
            g.handle(url, {}, "L", client_headers={"Host": "evil.com"})
        _, kwargs = m.call_args
        assert "Host" not in kwargs["headers"]

    def test_different_referer_different_cache_key(self):
        """不同 Referer 产生不同 cache key，不串缓存。"""
        g = _make_gateway()
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        with patch("sgw.proxy.requests.get", return_value=_fake_resp()) as m:
            g.handle(url, {}, "L", client_headers={"Referer": "https://a"})
            g.handle(url, {}, "L", client_headers={"Referer": "https://b"})
        # 不同 Referer → 不同 cache key → 都打外网
        assert m.call_count == 2

    def test_same_referer_hits_cache(self):
        """相同 Referer 第二次命中缓存。"""
        g = _make_gateway()
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        with patch("sgw.proxy.requests.get", return_value=_fake_resp()):
            _, _, h1 = g.handle(url, {}, "L", client_headers={"Referer": "https://a"})
            _, _, h2 = g.handle(url, {}, "L", client_headers={"Referer": "https://a"})
        assert h1["X-Cache"] == "MISS"
        assert h2["X-Cache"] == "HIT-MEM"

    def test_no_client_headers_backward_compat(self):
        """不传 client_headers（旧调用方式）仍正常工作，用默认 UA。"""
        g = _make_gateway()
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        with patch("sgw.proxy.requests.get", return_value=_fake_resp()) as m:
            g.handle(url, {}, "L")  # 不传 client_headers
        _, kwargs = m.call_args
        assert "User-Agent" in kwargs["headers"]
