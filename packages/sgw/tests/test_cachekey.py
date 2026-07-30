"""sgw cache key 规范化测试。

验证 _canonical_url 与 cache key 的核心正确性：
- 不同 params（股票/日期/页码）产生不同 cache key，杜绝串缓存
- params 顺序不同但内容相同 → 同一 key
- target_url 自带 query 与 params 合并后规范
- group_of 仍拒绝未归组的 host（回归）

测试方法见 .agents/notes/test-method.md；本文件是其 L1 之外的自动化补充。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from sgw.proxy import Gateway, _canonical_url, load_config


# ── _canonical_url 单元 ──────────────────────────────────────
class TestCanonicalUrl:
    def test_different_params_different_key(self):
        """核心正确性：不同股票代码不能串缓存。"""
        a = _canonical_url("https://dc-web.eastmoney.com/api/data/v1/get", {"code": "600519"})
        b = _canonical_url("https://dc-web.eastmoney.com/api/data/v1/get", {"code": "000001"})
        assert a != b

    def test_param_order_irrelevant(self):
        """params 字典顺序不同但内容相同 → 同一 key。"""
        u = "https://dc-web.eastmoney.com/api/data/v1/get"
        a = _canonical_url(u, {"a": "1", "b": "2", "c": "3"})
        b = _canonical_url(u, {"c": "3", "a": "1", "b": "2"})
        assert a == b

    def test_url_query_merged_with_params(self):
        """target_url 自带 query 与 params 合并；params 覆盖同名。"""
        a = _canonical_url("https://h.eastmoney.com/p?a=1", {"b": "2"})
        b = _canonical_url("https://h.eastmoney.com/p", {"a": "1", "b": "2"})
        assert a == b

    def test_params_override_url_query(self):
        """params 中同名 key 覆盖 url 自带 query 值。"""
        a = _canonical_url("https://h.eastmoney.com/p?code=600519", {"code": "000001"})
        b = _canonical_url("https://h.eastmoney.com/p", {"code": "000001"})
        assert a == b

    def test_no_params_strips_query_when_canonical(self):
        """无 params 时仍规范化（query 排序）。"""
        a = _canonical_url("https://h.eastmoney.com/p?b=2&a=1")
        b = _canonical_url("https://h.eastmoney.com/p?a=1&b=2")
        assert a == b

    def test_different_page_different_key(self):
        """分页场景：不同页码不能串缓存。"""
        u = "https://dc-web.eastmoney.com/api/data/v1/get"
        p1 = _canonical_url(u, {"pageNumber": "1"})
        p2 = _canonical_url(u, {"pageNumber": "2"})
        assert p1 != p2


# ── Gateway handle 串缓存回归 ──────────────────────────────
def _make_gateway() -> Gateway:
    cfg = load_config(__import__("pathlib").Path(__file__).resolve().parent.parent / "sgw" / "config.toml")
    cfg.setdefault("cache", {})["persist"] = {"enabled": False}
    return Gateway(cfg)


def _fake_resp(body: bytes = b'{"data":1}') -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.content = body
    r.headers = {"Content-Type": "application/json"}
    return r


class TestGatewayCacheKeyParams:
    def test_different_code_not_cross_cached(self):
        """同 URL 不同 code 参数：第一次 MISS 各自打外网，不应共享缓存。"""
        g = _make_gateway()
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        with patch("sgw.proxy.requests.get", return_value=_fake_resp(b'{"a":1}')) as m:
            g.handle(url, {"code": "600519"}, "L")
            g.handle(url, {"code": "000001"}, "L")
        # 两次都应实际请求外网（无串缓存）
        assert m.call_count == 2

    def test_same_code_second_hits_cache(self):
        """相同 URL+params 第二次命中内存缓存。"""
        g = _make_gateway()
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        with patch("sgw.proxy.requests.get", return_value=_fake_resp()):
            _, _, h1 = g.handle(url, {"code": "600519"}, "L")
            _, _, h2 = g.handle(url, {"code": "600519"}, "L")
        assert h1["X-Cache"] == "MISS"
        assert h2["X-Cache"] == "HIT-MEM"

    def test_different_page_not_cross_cached(self):
        """同 URL 不同 pageNumber：分页不串缓存。"""
        g = _make_gateway()
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        with patch("sgw.proxy.requests.get", return_value=_fake_resp()) as m:
            g.handle(url, {"pageNumber": "1"}, "L")
            g.handle(url, {"pageNumber": "2"}, "L")
        assert m.call_count == 2


# ── group_of 回归（确保未破坏 host 路由）─────────────────
class TestGroupOfRegression:
    def test_proxied_eastmoney_routable(self):
        g = _make_gateway()
        assert g.group_of("datacenter-web.eastmoney.com") == "eastmoney"

    def test_lookalike_host_rejected(self):
        """evil-eastmoney.com 后缀相似但非 .eastmoney.com，应被拒绝。"""
        g = _make_gateway()
        assert g.group_of("evil-eastmoney.com") is None
        assert g.group_of("not.eastmoney.com.evil.com") is None

    def test_non_proxied_domain_rejected(self):
        g = _make_gateway()
        assert g.group_of("legulegu.com") is None

    # ── exchange 组（非后缀源，第二层 exact-host 归组）──
    def test_szse_routable_via_exchange_group(self):
        """www.szse.cn 非东财/同花顺后缀，但经第二层 config 归组到 exchange。"""
        g = _make_gateway()
        assert g.group_of("www.szse.cn") == "exchange"

    def test_sse_routable_via_exchange_group(self):
        g = _make_gateway()
        assert g.group_of("query.sse.com.cn") == "exchange"

    def test_unknown_domain_still_rejected(self):
        """非后缀且未在 config 的域名仍被拒绝。"""
        g = _make_gateway()
        assert g.group_of("random.example.com") is None

    # ── inventory 涉及的新归组 host ──
    def test_emweb_securities_routable(self):
        """AKP-HOLD-001/002 十大股东用的 host（emweb.securities）。"""
        g = _make_gateway()
        assert g.group_of("emweb.securities.eastmoney.com") == "eastmoney"

    def test_datacenter_securities_routable(self):
        """AKP-EARN-001/002 业绩用的 host（datacenter.eastmoney.com）。"""
        g = _make_gateway()
        assert g.group_of("datacenter.eastmoney.com") == "eastmoney"

    def test_push2_unnumbered_routable(self):
        """AKP-BOARD 板块接口主用无编号 push2（已归组，回归确认）。"""
        g = _make_gateway()
        assert g.group_of("push2.eastmoney.com") == "eastmoney"

    def test_push2_numbered_not_in_group(self):
        """编号 push2 子域（29./79./91.）不归组——主用无编号，编号仅备选。"""
        g = _make_gateway()
        assert g.group_of("29.push2.eastmoney.com") is None
        assert g.group_of("79.push2.eastmoney.com") is None

    def test_handle_routes_new_hosts(self):
        """emweb/datacenter 经 handle 不再返回 400 domain not proxied。"""
        g = _make_gateway()
        with patch("sgw.proxy.requests.get", return_value=_fake_resp()):
            s1, _, _ = g.handle("https://emweb.securities.eastmoney.com/PC_HSF10/x", {}, "L")
            s2, _, _ = g.handle("https://datacenter.eastmoney.com/securities/api/x", {}, "L")
        assert s1 != 400  # 不再被 host-missing 拒绝
        assert s2 != 400
