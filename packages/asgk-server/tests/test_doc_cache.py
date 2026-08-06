"""DocumentCache 测试（§3.7 文档型缓存：bytes 文件 + 体积上限 + LRU 淘汰）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from asgk_server.cache import DocumentCache


@pytest.fixture
def doc_dir(tmp_path) -> Path:
    return tmp_path / "cache"


@pytest.fixture
def cache(doc_dir) -> DocumentCache:
    return DocumentCache(doc_dir, max_total_bytes=1000, max_file_bytes=400)


class TestDocumentCacheBasic:
    def test_set_get_roundtrip(self, cache):
        """写入 bytes，读取一致；ext 保留。"""
        assert cache.set("doc1", b"PDF-CONTENT\x00\x01", "pdf", ttl=300)
        result = cache.get("doc1")
        assert result is not None
        data, ext = result
        assert data == b"PDF-CONTENT\x00\x01"
        assert ext == "pdf"

    def test_miss_returns_none(self, cache):
        assert cache.get("nonexistent") is None

    def test_expired_returns_none_and_deletes(self, cache):
        """过期文档：get 返回 None 且删文件。"""
        cache.set("doc1", b"x" * 10, "pdf", ttl=100)
        # 手动改过期
        cache._index["doc1"]["expire"] = 0
        assert cache.get("doc1") is None
        assert "doc1" not in cache._index

    def test_persist_across_instances(self, doc_dir):
        """新实例加载旧 index（持久化）。"""
        c1 = DocumentCache(doc_dir, max_total_bytes=1000, max_file_bytes=400)
        c1.set("doc1", b"hello", "pdf", ttl=3600)
        c2 = DocumentCache(doc_dir, max_total_bytes=1000, max_file_bytes=400)
        result = c2.get("doc1")
        assert result is not None
        assert result[0] == b"hello"


class TestDocumentCacheLimits:
    def test_reject_oversize_file(self, cache):
        """单文件超 max_file_bytes 拒绝写（返 False）。"""
        big = b"x" * 401  # 上限 400
        assert cache.set("big", big, "pdf", ttl=300) is False
        assert cache.get("big") is None

    def test_lru_eviction_when_total_exceeds(self, cache):
        """总量超 max_total 时 LRU 淘汰最久未访问的。"""
        # max_total=1000，写 3 个各 400 字节（总 1200 > 1000）
        cache.set("a", b"a" * 400, "pdf", ttl=3600)
        cache.set("b", b"b" * 400, "pdf", ttl=3600)
        # 访问 a 刷新 atime（让 b 成为最旧的）
        cache.get("a")
        cache.set("c", b"c" * 400, "pdf", ttl=3600)  # 这会淘汰 b（最旧）
        assert cache.get("a") is not None  # a 保留（刚访问）
        assert cache.get("b") is None      # b 被 LRU 淘汰
        assert cache.get("c") is not None

    def test_replace_same_doc_updates_size(self, cache):
        """同 doc_id 重写：旧体积扣除，新体积计入。"""
        cache.set("doc1", b"x" * 100, "pdf", ttl=3600)
        before = cache._total_bytes
        cache.set("doc1", b"y" * 200, "pdf", ttl=3600)
        assert cache._total_bytes == before + 100  # 100 -> 200
        result = cache.get("doc1")
        assert result[0] == b"y" * 200


class TestDocumentCacheStats:
    def test_stats_reports_usage(self, cache):
        cache.set("doc1", b"x" * 100, "pdf", ttl=3600)
        cache.set("doc2", b"y" * 50, "pdf", ttl=3600)
        s = cache.stats()
        assert s["docs"] == 2
        assert s["total_bytes"] == 150
        assert s["max_total_bytes"] == 1000
