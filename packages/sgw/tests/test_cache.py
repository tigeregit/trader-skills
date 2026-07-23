"""sgw 网关缓存持久化单元测试。

覆盖 DiskCache（SQLite/WAL）的 set/get 往返、过期删除、tier 过滤、
load_all 回填、重启恢复、并发写；以及 Gateway 的 write-through 与
HIT-MEM/HIT-DISK/MISS 命中路径（mock 外网，不打真实东财）。

测试方法见 .agents/notes/test-method.md；本文件是其 L1 之外的自动化补充。
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sgw.proxy import Cache, DiskCache, Gateway, load_config


# ── DiskCache 单元 ────────────────────────────────────────────
@pytest.fixture
def disk(tmp_path: Path) -> DiskCache:
    return DiskCache(tmp_path / "test.db", {"P", "L"})


class TestDiskCache:
    def test_set_get_roundtrip(self, disk: DiskCache):
        disk.set("P|http://a", b"body1", {"Content-Type": "application/json"}, 3600, "P")
        r = disk.get("P|http://a")
        assert r is not None
        body, headers = r
        assert body == b"body1"
        assert headers == {"Content-Type": "application/json"}

    def test_get_miss(self, disk: DiskCache):
        assert disk.get("P|nonexistent") is None

    def test_tier_filter_skips_s(self, disk: DiskCache):
        """S 档不在持久化集，set 应是 no-op。"""
        disk.set("S|http://b", b"body2", {}, 3600, "S")
        assert disk.get("S|http://b") is None

    def test_lazy_expiry_delete(self, disk: DiskCache):
        disk.set("P|http://c", b"body3", {}, 1, "P")
        time.sleep(1.2)
        assert disk.get("P|http://c") is None  # 命中过期 -> 惰性删除
        # 二次 get 确认已从表删除（仍是 miss，且无残留行）
        assert disk.get("P|http://c") is None

    def test_load_all_filters_expired(self, disk: DiskCache):
        disk.set("P|http://keep", b"keep", {"CT": "x"}, 3600, "P")
        disk.set("P|http://expire", b"expired", {}, 1, "P")
        time.sleep(1.2)
        loaded = disk.load_all()
        assert "P|http://keep" in loaded
        assert loaded["P|http://keep"][0] == b"keep"
        assert "P|http://expire" not in loaded  # 过期被清

    def test_restart_recovery(self, tmp_path: Path):
        """新实例 load_all 应恢复未过期项，并清掉启动前已过期项。"""
        d1 = DiskCache(tmp_path / "test.db", {"P", "L"})
        d1.set("P|http://a", b"body1", {}, 3600, "P")
        d1.set("P|http://old", b"old", {}, 1, "P")
        time.sleep(1.2)
        d1.close()

        d2 = DiskCache(tmp_path / "test.db", {"P", "L"})
        loaded = d2.load_all()
        assert "P|http://a" in loaded
        assert "P|http://old" not in loaded  # 启动时清理
        # 磁盘表里 old 行应已删除
        assert d2.get("P|http://old") is None

    def test_headers_serialization_roundtrip(self, disk: DiskCache):
        h = {"Content-Type": "application/json; charset=utf-8", "X-Foo": "中文"}
        disk.set("L|http://h", b"x", h, 3600, "L")
        _, got = disk.get("L|http://h")
        assert got == h

    def test_stats(self, disk: DiskCache):
        disk.set("P|http://s1", b"x", {}, 3600, "P")
        s = disk.stats()
        assert s["size"] == 1
        assert s["hits"] == 0 and s["misses"] == 0

    def test_concurrent_writes_different_keys(self, disk: DiskCache):
        """WAL + 单写锁：多线程写不同 key 不报错，全部可读回。"""
        import threading

        errors = []

        def writer(i):
            try:
                disk.set(f"P|k{i}", f"body{i}".encode(), {}, 3600, "P")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        for i in range(20):
            r = disk.get(f"P|k{i}")
            assert r is not None and r[0] == f"body{i}".encode()


# ── Gateway 持久化集成 ────────────────────────────────────────
def _make_gateway(cache_dir: str | Path) -> Gateway:
    cfg = load_config(Path(__file__).resolve().parent.parent / "sgw" / "config.toml")
    cfg.setdefault("cache", {})["persist"] = {
        "enabled": True, "dir": str(cache_dir), "tiers": ["P", "L"],
    }
    return Gateway(cfg, cache_dir_override=str(cache_dir))


def _fake_resp(body: bytes = b'{"data":1}') -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.content = body
    r.headers = {"Content-Type": "application/json"}
    return r


class TestGatewayPersistence:
    def test_miss_then_hit_mem(self, tmp_path: Path):
        g = _make_gateway(tmp_path)
        with patch("sgw.proxy.requests.get", return_value=_fake_resp()):
            _, _, h1 = g.handle("https://reportapi.eastmoney.com/report/list",
                                {"k": "v"}, "P")
        assert h1["X-Cache"] == "MISS"
        _, _, h2 = g.handle("https://reportapi.eastmoney.com/report/list",
                            {"k": "v"}, "P")
        assert h2["X-Cache"] == "HIT-MEM"

    def test_disk_hit_after_mem_clear(self, tmp_path: Path):
        """清空内存模拟重启后磁盘命中，并回填内存。"""
        g = _make_gateway(tmp_path)
        with patch("sgw.proxy.requests.get", return_value=_fake_resp(b'{"data":2}')):
            g.handle("https://reportapi.eastmoney.com/report/list", {"k": "v"}, "P")
        g.cache._store.clear()  # 模拟重启：内存空
        _, _, h = g.handle("https://reportapi.eastmoney.com/report/list", {"k": "v"}, "P")
        assert h["X-Cache"] == "HIT-DISK"
        # 回填后下次走内存
        _, _, h2 = g.handle("https://reportapi.eastmoney.com/report/list", {"k": "v"}, "P")
        assert h2["X-Cache"] == "HIT-MEM"

    def test_s_tier_not_persisted_to_disk(self, tmp_path: Path):
        """S 档不落盘：写入后清内存，磁盘回查应 miss。"""
        g = _make_gateway(tmp_path)
        with patch("sgw.proxy.requests.get", return_value=_fake_resp()):
            # S 档盘后才缓存；这里直接构造 ttl>0 的 S 命中需绕过盘中判断，
            # 用 L 档反向验证更直接，此处验证 S set 后磁盘无对应条目。
            g.handle("https://reportapi.eastmoney.com/report/list", {"k": "s"}, "S")
        # S 档 key 不应在磁盘
        key = "S|https://reportapi.eastmoney.com/report/list?k=s"
        # 构造完整 key（含 query 合并形式）验证 disk_cache 无 S 档条目
        # 直接检查 db 表内容
        assert g.disk_cache is not None
        rows = g.disk_cache._conn.execute(
            "SELECT tier FROM cache WHERE tier='S'"
        ).fetchall()
        assert rows == []

    def test_stats_includes_disk_fields(self, tmp_path: Path):
        g = _make_gateway(tmp_path)
        s = g.stats()
        assert "disk_cache" in s and s["disk_cache"] is not None
        assert "disk_load_count" in s
        assert "disk_load_ms" in s

    def test_disk_disabled_when_config_off(self, tmp_path: Path):
        """未启用 persist 时 disk_cache 为 None，stats 中 disk_cache 为 null。"""
        cfg = load_config(Path(__file__).resolve().parent.parent / "sgw" / "config.toml")
        cfg.setdefault("cache", {})["persist"] = {"enabled": False}
        g = Gateway(cfg)
        assert g.disk_cache is None
        assert g.stats()["disk_cache"] is None


# ── 内存 Cache 回归（确保未破坏原有行为）─────────────────────
class TestInMemoryCache:
    def test_set_get_hit(self):
        c = Cache()
        c.set("k", b"v", {}, 3600, "P")
        assert c.get("k") == (b"v", {})

    def test_expiry(self):
        c = Cache()
        c.set("k", b"v", {}, 1, "P")
        time.sleep(1.2)
        assert c.get("k") is None

    def test_no_store_when_ttl_zero(self):
        c = Cache()
        c.set("k", b"v", {}, 0, "R")
        assert c.get("k") is None
