"""熔断状态持久化与状态库安全闩测试（不访问真实上游）。"""
from __future__ import annotations

import json
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sgw.proxy import CircuitBreaker, CircuitStateManager, Gateway, load_config


CONFIG = Path(__file__).resolve().parent.parent / "sgw" / "config.toml"
PUBLIC_URL = "https://reportapi.eastmoney.com/report/list"
BACKOFF = (600, 1800, 3600, 21600, 43200, 86400)


class FakeClock:
    def __init__(self, now: float = 1_000_000):
        self.value = now

    def __call__(self) -> float:
        return self.value


class FailingFactory:
    def __init__(self):
        self.calls = 0

    def __call__(self, _path):
        self.calls += 1
        raise OSError("state storage unavailable")


class MemoryStore:
    def __init__(self):
        self.states = {}
        self.probes = 0
        self.closed = False

    def load_all(self):
        return self.states

    def save_all(self, states, _now):
        self.states = states

    def health_probe(self, _now):
        self.probes += 1

    def close(self):
        self.closed = True


class SwitchFactory:
    def __init__(self):
        self.available = False
        self.calls = 0
        self.store = MemoryStore()

    def __call__(self, _path):
        self.calls += 1
        if not self.available:
            raise OSError("state storage unavailable")
        return self.store


def _gateway_config(state_dir: Path, *, cooldown: float = 300) -> dict:
    cfg = load_config(CONFIG)
    cfg["cache"]["persist"] = {"enabled": False}
    cfg["fingerprint"]["enabled"] = False
    cfg["state"] = {
        "enabled": True,
        "dir": str(state_dir),
        "backoff_seconds": list(BACKOFF),
    }
    cfg["retry"] = {"max_attempts": 1}
    cfg["circuit"]["cooldown_seconds"] = cooldown
    for group in cfg["group"]:
        group["rps"] = 100000
        group["jitter"] = [0, 0]
    return cfg


def _response(status: int, body: bytes = b'{"ok":true}') -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.content = body
    response.headers = {"Content-Type": "application/json"}
    return response


class TestStateBackoff:
    def test_exact_schedule_and_24_hour_cap(self, tmp_path):
        clock = FakeClock()
        factory = FailingFactory()
        manager = CircuitStateManager(
            tmp_path, {"eastmoney"}, BACKOFF, clock, factory
        )
        assert factory.calls == 1

        for expected_stage in range(len(BACKOFF)):
            stats = manager.stats()
            assert stats["backoff_stage"] == expected_stage
            assert stats["next_probe_at"] == pytest.approx(
                clock.value + BACKOFF[expected_stage]
            )

            clock.value = stats["next_probe_at"] - 0.001
            assert manager.before_egress() == (False, None)
            calls_before = factory.calls

            clock.value += 0.001
            assert manager.before_egress() == (False, None)
            assert factory.calls == calls_before + 1

        stats = manager.stats()
        assert stats["backoff_stage"] == len(BACKOFF) - 1
        assert stats["next_probe_at"] == pytest.approx(clock.value + 86400)
        assert stats["first_failure_at"] == 1_000_000
        assert stats["last_failure_at"] == clock.value

    def test_recovery_probe_is_storage_only_and_resets_state(self, tmp_path):
        clock = FakeClock()
        factory = SwitchFactory()
        manager = CircuitStateManager(
            tmp_path, {"eastmoney"}, BACKOFF, clock, factory
        )
        clock.value += 600
        factory.available = True

        allowed, states = manager.before_egress()

        assert allowed is True
        assert states == {}
        assert factory.store.probes == 1
        assert manager.stats()["ready"] is True
        assert manager.stats()["last_success_at"] == clock.value
        manager.close()

    def test_1000_concurrent_callers_run_one_due_probe(self, tmp_path):
        clock = FakeClock()
        factory = FailingFactory()
        manager = CircuitStateManager(
            tmp_path, {"eastmoney"}, BACKOFF, clock, factory
        )
        clock.value += 600

        with ThreadPoolExecutor(max_workers=1000) as pool:
            results = list(pool.map(lambda _i: manager.before_egress(), range(1000)))

        assert all(result == (False, None) for result in results)
        assert factory.calls == 2  # 启动一次 + 到期健康探测一次
        assert manager.stats()["backoff_stage"] == 1

    def test_corrupt_db_and_marker_fail_closed_for_24_hours(self, tmp_path):
        (tmp_path / "sgw_safety_latch.json").write_text("not-json")
        (tmp_path / "sgw_state.db").write_bytes(b"not-sqlite")
        clock = FakeClock()

        manager = CircuitStateManager(
            tmp_path, {"eastmoney"}, BACKOFF, clock
        )

        stats = manager.stats()
        assert stats["ready"] is False
        assert stats["backoff_stage"] == 5
        assert stats["next_probe_at"] == clock.value + 86400
        assert manager.before_egress() == (False, None)

    def test_single_corrupt_marker_starts_at_10_minutes(self, tmp_path):
        (tmp_path / "sgw_safety_latch.json").write_text("not-json")
        clock = FakeClock()

        manager = CircuitStateManager(
            tmp_path, {"eastmoney"}, BACKOFF, clock
        )

        stats = manager.stats()
        assert stats["ready"] is False
        assert stats["backoff_stage"] == 0
        assert stats["next_probe_at"] == clock.value + 600
        manager.close()

    def test_single_corrupt_db_starts_at_10_minutes(self, tmp_path):
        marker = {
            "version": 1,
            "probe_state": "recovered",
            "first_failure_at": None,
            "last_failure_at": None,
            "last_success_at": 999_000,
            "backoff_stage": 0,
            "next_probe_at": 0,
            "circuits": {},
        }
        (tmp_path / "sgw_safety_latch.json").write_text(json.dumps(marker))
        (tmp_path / "sgw_state.db").write_bytes(b"not-sqlite")
        clock = FakeClock()

        manager = CircuitStateManager(
            tmp_path, {"eastmoney"}, BACKOFF, clock
        )

        stats = manager.stats()
        assert stats["ready"] is False
        assert stats["backoff_stage"] == 0
        assert stats["next_probe_at"] == clock.value + 600


class TestCircuitRestart:
    def test_403_circuit_survives_restart(self, tmp_path):
        cfg = _gateway_config(tmp_path)
        first = Gateway(cfg)
        with patch("sgw.proxy.requests.get", return_value=_response(403)) as upstream:
            assert first.handle(PUBLIC_URL, {"page": 1}, "R")[0] == 403
        assert upstream.call_count == 1
        first.close()

        restarted = Gateway(cfg)
        with patch("sgw.proxy.requests.get", return_value=_response(200)) as upstream:
            status, _, _ = restarted.handle(PUBLIC_URL, {"page": 2}, "R")
        assert status == 503
        upstream.assert_not_called()
        assert restarted.stats()["circuits"]["eastmoney"]["last_status"] == 403
        restarted.close()

    def test_accumulated_5xx_failures_survive_restarts(self, tmp_path):
        cfg = _gateway_config(tmp_path)
        for page in (1, 2, 3):
            gateway = Gateway(cfg)
            with patch("sgw.proxy.requests.get", return_value=_response(500)) as upstream:
                assert gateway.handle(PUBLIC_URL, {"page": page}, "R")[0] == 502
            assert upstream.call_count == 1
            gateway.close()

        restarted = Gateway(cfg)
        with patch("sgw.proxy.requests.get", return_value=_response(200)) as upstream:
            assert restarted.handle(PUBLIC_URL, {"page": 4}, "R")[0] == 503
        upstream.assert_not_called()
        assert restarted.stats()["circuits"]["eastmoney"]["failures"] == 3
        restarted.close()

    def test_probe_lease_blocks_restart_during_canary(self, tmp_path):
        clock = FakeClock()
        manager = CircuitStateManager(
            tmp_path, {"eastmoney"}, BACKOFF, clock
        )
        breaker = CircuitBreaker(10, 3, clock, probe_lease=120)
        breaker._on_change = lambda: manager.save_all(
            {"eastmoney": breaker.snapshot()}
        )
        breaker.failure(immediate=True, status=429)
        clock.value += 10
        assert breaker.before_request() is True
        manager.close()  # 模拟 canary 发出后进程退出

        restarted_manager = CircuitStateManager(
            tmp_path, {"eastmoney"}, BACKOFF, clock
        )
        restarted = CircuitBreaker(10, 3, clock, probe_lease=120)
        restarted.restore(restarted_manager.initial_states["eastmoney"])
        assert restarted.is_open() is True
        assert restarted.stats()["open_until"] == clock.value + 120
        restarted_manager.close()


class TestStateFailureSafety:
    def test_cache_remains_available_while_state_store_is_blocked(self, tmp_path):
        gateway = Gateway(_gateway_config(tmp_path))
        with patch("sgw.proxy.requests.get", return_value=_response(200)) as upstream:
            assert gateway.handle(PUBLIC_URL, {"page": 1}, "P")[0] == 200
        assert upstream.call_count == 1

        assert gateway.state_manager is not None
        assert gateway.state_manager.store is not None
        with patch.object(
            gateway.state_manager.store,
            "save_all",
            side_effect=OSError("disk full"),
        ):
            assert gateway._persist_circuit_states() is False

        with patch("sgw.proxy.requests.get", return_value=_response(200)) as upstream:
            cached = gateway.handle(PUBLIC_URL, {"page": 1}, "P")
            blocked = gateway.handle(PUBLIC_URL, {"page": 2}, "P")
        assert cached[0] == 200
        assert cached[2]["X-Cache"] == "HIT-MEM"
        assert blocked[0] == 503
        upstream.assert_not_called()
        gateway.close()

    def test_state_files_whitelist_fields_and_exclude_secrets(self, tmp_path):
        manager = CircuitStateManager(
            tmp_path, {"eastmoney"}, BACKOFF
        )
        secrets = (
            "cookie-plaintext",
            "csrf-plaintext",
            "https://example.test/private?token=plaintext",
        )
        assert manager.save_all({
            "eastmoney": {
                "open_until": 10,
                "probe_until": 20,
                "failures": 1,
                "opens": 2,
                "last_status": 429,
                "cookie": secrets[0],
                "csrf": secrets[1],
                "url": secrets[2],
            },
            secrets[0]: {"open_until": 99},
        }) is True
        manager.close()

        persisted = b"".join(
            path.read_bytes() for path in tmp_path.iterdir() if path.is_file()
        )
        assert all(secret.encode() not in persisted for secret in secrets)
        marker = json.loads((tmp_path / "sgw_safety_latch.json").read_text())
        assert stat.S_IMODE((tmp_path / "sgw_state.db").stat().st_mode) == 0o600
        assert stat.S_IMODE(
            (tmp_path / "sgw_safety_latch.json").stat().st_mode
        ) == 0o600
        assert set(marker["circuits"]) == {"eastmoney"}
        assert set(marker["circuits"]["eastmoney"]) == {
            "open_until", "probe_until", "failures", "opens", "last_status",
        }

    def test_single_attempt_config_disables_automatic_retry(self, tmp_path):
        gateway = Gateway(_gateway_config(tmp_path))
        with patch("sgw.proxy.requests.get", return_value=_response(500)) as upstream:
            assert gateway.handle(PUBLIC_URL, {"page": 1}, "R")[0] == 502
        assert upstream.call_count == 1
        gateway.close()
