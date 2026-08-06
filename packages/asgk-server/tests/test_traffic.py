"""asgk_server 流量内核单元测试。

覆盖从 sgw 搬入的三大基础设施（TokenBucket / SingleFlight / CircuitBreaker）
的核心行为，确保搬入后语义与 sgw 一致。完整状态闩（CircuitStateManager）行为
由 sgw 的 test_circuit_state.py 覆盖。

缓存（MemoryCache/JsonDiskCache/SemanticCache）的测试在 test_cache.py；
本文件只测限流/熔断/singleflight。

测试方法见 .agents/notes/test-method.md。
"""
from __future__ import annotations

import time

from asgk_server.traffic import (
    CircuitBreaker,
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
