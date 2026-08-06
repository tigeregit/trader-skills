"""asgk_server 缓存机制单元测试（§3.6）。

覆盖 T1.5 的四点改造：
  a. 存结构化数据（dict/list），非原始字节
  b. per-source 独立缓存（不跨源共享）
  c. 六类数据型差异化 TTL/落盘（取代五档一刀切）
  d. JSON 文件磁盘持久化（取代 SQLite）

验收对应（plan T1.5）：
  - per-source 缓存隔离
  - 六类分档 TTL 正确
  - 磁盘持久化（定稿+季度落盘，重启恢复；实时/流式不落盘）
  - singleflight 对 realtime(TTL=0) 仍合并并发（在 test_server.py 覆盖）

测试方法见 .agents/notes/test-method.md。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from asgk_server.cache import (
    JsonDiskCache,
    MemoryCache,
    SemanticCache,
    semantic_key,
)
from asgk_server.cache_policy import (
    is_structured,
    resolve_ttl,
    should_persist,
)


# ── semantic_key：规范化与 per-source 隔离（§3.6b/f）──────────
class TestSemanticKey:
    def test_codes_order_independent(self):
        """codes 列表顺序无关：集合语义等价。"""
        k1 = semantic_key("quote", "tencent", {"codes": ["600519", "000001"]})
        k2 = semantic_key("quote", "tencent", {"codes": ["000001", "600519"]})
        assert k1 == k2

    def test_codes_dedup(self):
        k1 = semantic_key("quote", "tencent", {"codes": ["600519", "600519"]})
        k2 = semantic_key("quote", "tencent", {"codes": ["600519"]})
        assert k1 == k2

    def test_different_source_different_key(self):
        """per-source 独立：不同源不同 key（§3.6b 核心）。"""
        k1 = semantic_key("quote", "tencent", {"codes": ["600519"]})
        k2 = semantic_key("quote", "sina", {"codes": ["600519"]})
        assert k1 != k2

    def test_different_capability_different_key(self):
        k1 = semantic_key("quote", "tencent", {"codes": ["600519"]})
        k2 = semantic_key("kline", "tencent", {"code": "600519"})
        assert k1 != k2

    def test_different_params_different_key(self):
        k1 = semantic_key("quote", "tencent", {"codes": ["600519"]})
        k2 = semantic_key("quote", "tencent", {"codes": ["000001"]})
        assert k1 != k2

    def test_dict_key_order_independent(self):
        """dict 参数的 key 顺序无关。"""
        k1 = semantic_key("cap", "src", {"a": 1, "b": 2})
        k2 = semantic_key("cap", "src", {"b": 2, "a": 1})
        assert k1 == k2

    def test_key_format(self):
        """key = capability|source|16位hash。"""
        k = semantic_key("quote", "tencent", {"codes": ["600519"]})
        parts = k.split("|")
        assert len(parts) == 3
        assert parts[0] == "quote"
        assert parts[1] == "tencent"
        assert len(parts[2]) == 16


# ── MemoryCache：结构化数据存取 ──────────────────────────────
class TestMemoryCache:
    def test_set_get_structured(self):
        """存 dict/list（非字节），命中即返原对象语义。"""
        c = MemoryCache()
        c.set("k", {"price": 100.0, "name": "贵州茅台"}, ttl=3600)
        r = c.get("k")
        assert r == {"price": 100.0, "name": "贵州茅台"}

    def test_get_miss(self):
        c = MemoryCache()
        assert c.get("nope") is None
        assert c.misses == 1

    def test_expiry(self):
        c = MemoryCache()
        c.set("k", [1, 2, 3], ttl=1)
        time.sleep(1.2)
        assert c.get("k") is None

    def test_zero_ttl_not_stored(self):
        c = MemoryCache()
        c.set("k", "v", ttl=0)
        assert c.get("k") is None

    def test_stats(self):
        c = MemoryCache()
        c.set("k", "v", ttl=3600)
        c.get("k")
        c.get("miss")
        s = c.stats()
        assert s["size"] == 1
        assert s["hits"] == 1
        assert s["misses"] == 1


# ── JsonDiskCache：JSON 文件落盘（§3.6d）─────────────────────
class TestJsonDiskCache:
    def test_set_get_roundtrip(self, tmp_path: Path):
        dc = JsonDiskCache(tmp_path / "cache")
        dc.set("quote|tencent|abc123", {"price": 100.0}, ttl=3600)
        r = dc.get("quote|tencent|abc123")
        assert r == {"price": 100.0}

    def test_file_path_layout(self, tmp_path: Path):
        """路径 = cache_dir/capability/source/hash.json（三级目录）。"""
        dc = JsonDiskCache(tmp_path / "cache")
        dc.set("quote|tencent|abc123def456", {"x": 1}, ttl=3600)
        path = tmp_path / "cache" / "quote" / "tencent" / "abc123def456.json"
        assert path.exists()

    def test_get_miss(self, tmp_path: Path):
        dc = JsonDiskCache(tmp_path / "cache")
        assert dc.get("nonexistent") is None

    def test_lazy_expiry_deletes_file(self, tmp_path: Path):
        dc = JsonDiskCache(tmp_path / "cache")
        dc.set("q|t|h", {"v": 1}, ttl=1)
        path = tmp_path / "cache" / "q" / "t" / "h.json"
        assert path.exists()
        time.sleep(1.2)
        assert dc.get("q|t|h") is None  # 命中过期 → 惰性删除
        assert not path.exists()  # 文件被删

    def test_load_all_filters_expired(self, tmp_path: Path):
        """启动 load_all 回填未过期项，删过期文件。"""
        dc = JsonDiskCache(tmp_path / "cache")
        dc.set("cap|src|keep", {"k": 1}, ttl=3600)
        dc.set("cap|src|expire", {"e": 1}, ttl=1)
        time.sleep(1.2)
        # 模拟重启：新实例 load_all
        dc2 = JsonDiskCache(tmp_path / "cache")
        loaded = dc2.load_all()
        assert "cap|src|keep" in loaded
        assert loaded["cap|src|keep"][0] == {"k": 1}
        assert "cap|src|expire" not in loaded

    def test_corrupt_file_handled(self, tmp_path: Path):
        """损坏 JSON 文件被忽略并清理（cache 可重建）。"""
        dc = JsonDiskCache(tmp_path / "cache")
        path = tmp_path / "cache" / "cap" / "src" / "bad.json"
        path.parent.mkdir(parents=True)
        path.write_text("not valid json {{{", encoding="utf-8")
        assert dc.get("cap|src|bad") is None  # 不抛异常
        assert not path.exists()  # 清理掉


# ── SemanticCache：内存 + 磁盘组合 ───────────────────────────
class TestSemanticCache:
    def test_memory_hit_no_disk(self):
        sc = SemanticCache(disk_cache=None)
        sc.set("k", {"v": 1}, ttl=3600, persist=False)
        assert sc.get("k") == {"v": 1}

    def test_persist_writes_disk(self, tmp_path: Path):
        """persist=True 时 write-through 到磁盘。"""
        dc = JsonDiskCache(tmp_path / "cache")
        sc = SemanticCache(dc)
        sc.set("k", {"v": 1}, ttl=3600, persist=True)
        # 内存命中
        assert sc.get("k") == {"v": 1}
        # 磁盘也写了
        assert dc.get("k") == {"v": 1}

    def test_no_persist_skips_disk(self, tmp_path: Path):
        """persist=False 时只写内存，不落盘。"""
        dc = JsonDiskCache(tmp_path / "cache")
        sc = SemanticCache(dc)
        sc.set("k", {"v": 1}, ttl=3600, persist=False)
        assert sc.get("k") == {"v": 1}  # 内存有
        assert dc.get("k") is None  # 磁盘没有

    def test_disk_fallback_after_memory_miss(self, tmp_path: Path):
        """内存未命中时回查磁盘，命中后回填内存。"""
        dc = JsonDiskCache(tmp_path / "cache")
        sc = SemanticCache(dc)
        sc.set("k", {"v": 1}, ttl=3600, persist=True)
        # 清空内存模拟内存 miss
        sc.memory._store.clear()
        assert sc.get("k") == {"v": 1}  # 从磁盘回填

    def test_restart_recovery(self, tmp_path: Path):
        """重启后从磁盘 load_all 回填内存（定稿型场景）。"""
        dc = JsonDiskCache(tmp_path / "cache")
        sc = SemanticCache(dc)
        sc.set("announce|cninfo|h", {"title": "公告"}, ttl=3600, persist=True)
        # 模拟重启：新 SemanticCache，从同一磁盘目录回填
        dc2 = JsonDiskCache(tmp_path / "cache")
        sc2 = SemanticCache(dc2)
        for key, (value, expire) in dc2.load_all().items():
            sc2.preload(key, value, expire)
        assert sc2.get("announce|cninfo|h") == {"title": "公告"}


# ── cache_policy：六类分档 TTL/落盘（§3.6c）─────────────────
class TestCachePolicy:
    def test_definitive_30d_persist(self):
        assert resolve_ttl("definitive") == 30 * 86400
        assert should_persist("definitive") is True

    def test_quarterly_1d_persist(self):
        assert resolve_ttl("quarterly") == 86400
        assert should_persist("quarterly") is True

    def test_daily_settled_intraday_no_cache(self):
        """盘中 no-cache（避免脏数据）。"""
        assert resolve_ttl("daily_settled", lambda: True) == 0

    def test_daily_settled_afterclose_12h(self):
        """盘后定稿 12h；无 is_intraday_fn 默认按盘后。"""
        assert resolve_ttl("daily_settled", lambda: False) == 12 * 3600
        assert resolve_ttl("daily_settled") == 12 * 3600

    def test_daily_settled_not_persist(self):
        """盘后定稿型不落盘（盘中易脏）。"""
        assert should_persist("daily_settled") is False

    def test_daily_volatile_1h_not_persist(self):
        assert resolve_ttl("daily_volatile") == 3600
        assert should_persist("daily_volatile") is False

    def test_realtime_no_cache(self):
        assert resolve_ttl("realtime") == 0
        assert should_persist("realtime") is False

    def test_streaming_no_cache(self):
        assert resolve_ttl("streaming") == 0
        assert should_persist("streaming") is False

    def test_document_30d_persist_bytes(self):
        """文档型 30 天落盘，但存原始 bytes 非结构化。"""
        assert resolve_ttl("document") == 30 * 86400
        assert should_persist("document") is True
        assert is_structured("document") is False

    def test_unknown_policy_defaults_no_cache(self):
        assert resolve_ttl("bogus") == 0
        assert should_persist("bogus") is False


# ── 集成：落盘行为与数据型联动 ───────────────────────────────
class TestPersistByPolicy:
    def test_definitive_persists_realtime_does_not(self, tmp_path: Path):
        """定稿型落盘，实时型不落盘——同盘目录下只有定稿的文件。"""
        dc = JsonDiskCache(tmp_path / "cache")
        sc = SemanticCache(dc)
        # 定稿型：persist=True
        sc.set("announce|cninfo|h1", {"t": "公告"}, ttl=resolve_ttl("definitive"),
               persist=should_persist("definitive"))
        # 实时型：persist=False
        sc.set("quote|tencent|h2", {"p": 100}, ttl=resolve_ttl("realtime"),
               persist=should_persist("realtime"))
        # 磁盘上只有 announce
        files = list((tmp_path / "cache").rglob("*.json"))
        assert len(files) == 1
        assert "announce" in str(files[0])
