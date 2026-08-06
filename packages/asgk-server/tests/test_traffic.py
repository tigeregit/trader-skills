"""asgk_server 流量内核单元测试。

覆盖从 sgw 搬入的四大基础设施（TokenBucket / Cache / DiskCache / SingleFlight /
CircuitBreaker）的核心行为，确保零改搬入后语义与 sgw 一致。完整状态闩
（CircuitStateManager）行为由 sgw 的 test_circuit_state.py 覆盖，这里只验证
CapabilityServer 能正确装载流量内核（限流组/熔断/状态库初始化）。

测试方法见 .agents/notes/test-method.md。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from asgk_server.traffic import (
    Cache,
    CircuitBreaker,
    DiskCache,
    SingleFlight,
    TokenBucket,
)


# ── TokenBucket ───────────────────────────────────────────────
class TestTokenBucket:
    def test_first_acquire_no_wait(self):
        bucket = TokenBucket(rps=10, jitter=(0, 0))
        wait = bucket.acquire()
        assert wait == 0

    def test_serializes_burst(self):
        """两次紧邻的请求，第二次应至少等待 min_interval - jitter。"""
        bucket = TokenBucket(rps=10, jitter=(0, 0))  # min_interval = 0.1s
        bucket.acquire()
        t0 = time.time()
        bucket.acquire()
        elapsed = time.time() - t0
        # 实际睡眠应在 min_interval 附近（允许调度抖动）
        assert elapsed >= 0.08

    def test_jitter_adds_wait(self):
        bucket = TokenBucket(rps=5, jitter=(0.1, 0.1))  # min_interval=0.2 + jitter 0.1
        bucket.acquire()
        wait = bucket.acquire()
        assert wait >= 0.25  # 0.2 间隔 + 0.1 jitter


# ── Cache ─────────────────────────────────────────────────────
class TestCache:
    def test_set_get_roundtrip(self):
        c = Cache()
        c.set("k1", b"body", {"CT": "json"}, 3600, "P")
        r = c.get("k1")
        assert r is not None
        assert r[0] == b"body"
        assert r[1] == {"CT": "json"}

    def test_get_miss(self):
        c = Cache()
        assert c.get("nonexistent") is None
        assert c.misses == 1

    def test_expiry(self):
        c = Cache()
        c.set("k", b"v", {}, 1, "P")
        time.sleep(1.2)
        assert c.get("k") is None

    def test_zero_ttl_not_stored(self):
        c = Cache()
        c.set("k", b"v", {}, 0, "R")
        assert c.get("k") is None

    def test_stats(self):
        c = Cache()
        c.set("k", b"v", {}, 3600, "P")
        c.get("k")
        c.get("miss")
        s = c.stats()
        assert s["size"] == 1
        assert s["hits"] == 1
        assert s["misses"] == 1


# ── DiskCache ─────────────────────────────────────────────────
@pytest.fixture
def disk(tmp_path: Path) -> DiskCache:
    return DiskCache(tmp_path / "test.db", {"P", "L"})


class TestDiskCache:
    def test_set_get_roundtrip(self, disk: DiskCache):
        disk.set("P|a", b"body", {"CT": "json"}, 3600, "P")
        r = disk.get("P|a")
        assert r is not None
        assert r[0] == b"body"

    def test_tier_filter_skips_others(self, disk: DiskCache):
        disk.set("S|x", b"v", {}, 3600, "S")
        assert disk.get("S|x") is None

    def test_lazy_expiry(self, disk: DiskCache):
        disk.set("P|e", b"v", {}, 1, "P")
        time.sleep(1.2)
        assert disk.get("P|e") is None

    def test_load_all_filters_expired(self, disk: DiskCache):
        disk.set("P|keep", b"k", {}, 3600, "P")
        disk.set("P|expire", b"e", {}, 1, "P")
        time.sleep(1.2)
        loaded = disk.load_all()
        assert "P|keep" in loaded
        assert "P|expire" not in loaded


# ── SingleFlight ──────────────────────────────────────────────
class TestSingleFlight:
    def test_leader_then_follower(self):
        sf = SingleFlight()
        is_leader1, f1 = sf.join("key")
        is_leader2, f2 = sf.join("key")
        assert is_leader1 is True
        assert is_leader2 is False
        # leader 完成后 follower 才能拿到结果
        sf.finish("key", f1, (200, b"done", {}))
        assert f2.event.wait(timeout=1)
        assert f2.result == (200, b"done", {})
        assert sf.followers == 1

    def test_finish_clears_slot(self):
        sf = SingleFlight()
        _, f1 = sf.join("key")
        sf.finish("key", f1, (200, b"x", {}))
        # 重新 join 应是新的 leader
        is_leader2, _ = sf.join("key")
        assert is_leader2 is True


# ── CircuitBreaker ────────────────────────────────────────────
class TestCircuitBreaker:
    def test_success_keeps_closed(self):
        cb = CircuitBreaker(cooldown=10, failure_threshold=3)
        assert cb.is_open() is False
        assert cb.before_request() is True
        cb.success()
        assert cb.is_open() is False

    def test_403_opens_immediately(self):
        cb = CircuitBreaker(cooldown=10, failure_threshold=3)
        cb.failure(immediate=True, status=403)
        assert cb.is_open() is True
        assert cb.before_request() is False

    def test_failures_accumulate_to_threshold(self):
        cb = CircuitBreaker(cooldown=10, failure_threshold=3)
        cb.failure(status=500)
        cb.failure(status=500)
        assert cb.is_open() is False
        cb.failure(status=500)  # 第三次达到阈值
        assert cb.is_open() is True

    def test_success_resets(self):
        cb = CircuitBreaker(cooldown=10, failure_threshold=2)
        cb.failure(status=500)
        cb.success()
        cb.failure(status=500)  # 计数已重置，未达阈值
        assert cb.is_open() is False

    def test_snapshot_and_restore(self):
        cb = CircuitBreaker(cooldown=10, failure_threshold=3)
        cb.failure(immediate=True, status=429)
        snap = cb.snapshot()
        cb2 = CircuitBreaker(cooldown=10, failure_threshold=3)
        cb2.restore(snap)
        assert cb2.is_open() is True
