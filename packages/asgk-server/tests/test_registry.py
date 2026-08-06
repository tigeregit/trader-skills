"""asgk_server 能力注册表单元测试。

覆盖 @capability 装饰器、CapabilityMeta/SourceMeta 元数据校验、全局注册表的
登记/查询/枚举。验证 §3.2 契约：sources 可枚举、default_source 校验、data_type
取值约束、重复注册拒绝。
"""
from __future__ import annotations

import pytest

from asgk_server import registry
from asgk_server.registry import (
    CapabilityMeta,
    SourceMeta,
    capability,
    clear_registry,
    get_capability,
    list_capabilities,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个用例前后清空注册表，避免跨用例污染。"""
    clear_registry()
    yield
    clear_registry()


def _make_sources(*names: str, group="tencent") -> list[SourceMeta]:
    return [SourceMeta(name=n, group=group) for n in names]


class TestSourceMeta:
    def test_defaults(self):
        sm = SourceMeta(name="tencent", group="tencent")
        assert sm.egress_client == "requests"
        assert sm.healthy is True

    def test_curl_cffi_client(self):
        sm = SourceMeta(name="baidu", group="baidu", egress_client="curl_cffi")
        assert sm.egress_client == "curl_cffi"


class TestCapabilityMetaValidation:
    def test_valid_meta(self):
        meta = CapabilityMeta(
            name="quote", domain="行情",
            sources=_make_sources("tencent", "sina"),
            default_source="tencent", data_type="kv",
        )
        assert meta.source_names() == ["tencent", "sina"]
        assert meta.source("sina").group == "tencent"

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            CapabilityMeta(name="", domain="x", sources=_make_sources("a"),
                           default_source="a", data_type="kv")

    def test_invalid_data_type_rejected(self):
        with pytest.raises(ValueError, match="invalid data_type"):
            CapabilityMeta(name="q", domain="x", sources=_make_sources("a"),
                           default_source="a", data_type="bogus")

    def test_empty_sources_rejected(self):
        with pytest.raises(ValueError, match="at least one source"):
            CapabilityMeta(name="q", domain="x", sources=[],
                           default_source="x", data_type="kv")

    def test_duplicate_source_rejected(self):
        with pytest.raises(ValueError, match="duplicate source"):
            CapabilityMeta(name="q", domain="x",
                           sources=_make_sources("a", "a"),
                           default_source="a", data_type="kv")

    def test_default_source_not_in_sources_rejected(self):
        with pytest.raises(ValueError, match="default_source"):
            CapabilityMeta(name="q", domain="x", sources=_make_sources("a"),
                           default_source="b", data_type="kv")

    def test_source_unknown_raises(self):
        meta = CapabilityMeta(name="q", domain="x", sources=_make_sources("a"),
                              default_source="a", data_type="kv")
        with pytest.raises(KeyError):
            meta.source("nonexistent")


class TestDecorator:
    def test_registers_capability(self):
        @capability(name="quote", domain="行情",
                    sources=_make_sources("tencent", "sina"),
                    default_source="tencent", data_type="kv")
        def fetch_quote(ctx, codes, source=None):
            return {}

        meta, fn = get_capability("quote")
        assert meta.name == "quote"
        assert fn is fetch_quote

    def test_duplicate_registration_rejected(self):
        @capability(name="quote", domain="行情",
                    sources=_make_sources("tencent"),
                    default_source="tencent", data_type="kv")
        def fetch_quote(ctx, codes, source=None):
            return {}

        with pytest.raises(ValueError, match="already registered"):
            @capability(name="quote", domain="行情",
                        sources=_make_sources("sina"),
                        default_source="sina", data_type="kv")
            def fetch_quote2(ctx, codes, source=None):
                return {}

    def test_list_capabilities(self):
        @capability(name="quote", domain="行情",
                    sources=_make_sources("tencent"),
                    default_source="tencent", data_type="kv")
        def fetch_quote(ctx, codes, source=None):
            return {}

        @capability(name="kline", domain="行情",
                    sources=_make_sources("baidu"),
                    default_source="baidu", data_type="series")
        def fetch_kline(ctx, code, source=None):
            return {}

        caps = list_capabilities()
        assert set(caps) == {"quote", "kline"}
        assert caps["kline"].data_type == "series"

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError):
            get_capability("nonexistent")


class TestCachePolicyField:
    def test_default_cache_policy_realtime(self):
        meta = CapabilityMeta(name="q", domain="x", sources=_make_sources("a"),
                              default_source="a", data_type="kv")
        assert meta.cache_policy == "realtime"

    def test_custom_cache_policy(self):
        meta = CapabilityMeta(name="announce", domain="公告",
                              sources=_make_sources("cninfo"),
                              default_source="cninfo", data_type="kv",
                              cache_policy="definitive")
        assert meta.cache_policy == "definitive"
