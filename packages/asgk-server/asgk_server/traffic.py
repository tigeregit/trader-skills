"""asgk_server.traffic — 流量内核（限流/熔断/singleflight，从 sgw 搬入）。

三大流量基础设施，是能力代理服务端的出网安全内核：
  - TokenBucket：按域名组最小间隔限流（全局串行）
  - SingleFlight：并发 miss 合并为一次出网
  - CircuitBreaker / CircuitStateStore / CircuitStateManager：来源级熔断 +
    状态持久化 + 异常期安全闩（最该复用，避免状态库损坏时继续打上游）

缓存（Cache/DiskCache）不在本模块——T1.5 改造为能力语义缓存 cache.py
（结构化内存 + JSON 文件落盘，per-source）。熔断状态库仍用 SQLite（安全闩要 ACID）。
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_DEFAULT_STATE_BACKOFF = (600, 1800, 3600, 21600, 43200, 86400)


# ── SingleFlight ──────────────────────────────────────────────
@dataclass
class _Flight:
    event: threading.Event
    result: tuple[int, bytes, dict] | None = None


class SingleFlight:
    """把同一安全缓存身份的并发 miss 合并为一次上游请求。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._calls: dict[str, _Flight] = {}
        self.followers = 0

    def join(self, key: str) -> tuple[bool, _Flight]:
        with self._lock:
            flight = self._calls.get(key)
            if flight is not None:
                self.followers += 1
                return False, flight
            flight = _Flight(threading.Event())
            self._calls[key] = flight
            return True, flight

    def finish(self, key: str, flight: _Flight, result: tuple[int, bytes, dict]) -> None:
        flight.result = result
        flight.event.set()
        with self._lock:
            if self._calls.get(key) is flight:
                del self._calls[key]


# ── CircuitBreaker ────────────────────────────────────────────
class CircuitBreaker:
    """来源级熔断：403/429 立即开启，其余失败累计到阈值。"""

    def __init__(self, cooldown: float, failure_threshold: int,
                 now_fn=None, on_change=None, probe_lease: float = 120):
        self.cooldown = cooldown
        self.failure_threshold = failure_threshold
        self._now = now_fn or time.time
        self._on_change = on_change
        self.probe_lease = probe_lease
        self._lock = threading.Lock()
        self._open_until = 0.0
        self._probe_in_flight = False
        self._probe_until = 0.0
        self._failures = 0
        self._last_status: int | None = None
        self.opens = 0

    def is_open(self) -> bool:
        with self._lock:
            return max(self._open_until, self._probe_until) > self._now()

    def before_request(self) -> bool:
        """返回是否可出网；冷却后只允许一个 canary。"""
        changed = False
        with self._lock:
            now = self._now()
            if max(self._open_until, self._probe_until) > now:
                return False
            if self._open_until:
                if self._probe_in_flight:
                    return False
                self._probe_in_flight = True
                self._probe_until = now + self.probe_lease
                changed = True
        if changed and self._on_change and not self._on_change():
            with self._lock:
                self._probe_in_flight = False
                self._probe_until = 0.0
            return False
        return True

    def success(self) -> None:
        with self._lock:
            self._open_until = 0.0
            self._probe_in_flight = False
            self._probe_until = 0.0
            self._failures = 0
            self._last_status = None
        if self._on_change:
            self._on_change()

    def failure(self, immediate: bool = False, status: int | None = None) -> None:
        with self._lock:
            was_probe = self._probe_in_flight
            self._probe_in_flight = False
            self._probe_until = 0.0
            self._failures += 1
            self._last_status = status
            if immediate or was_probe or self._failures >= self.failure_threshold:
                self._open_until = self._now() + self.cooldown
                self.opens += 1
        if self._on_change:
            self._on_change()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "open_until": self._open_until,
                "probe_until": self._probe_until,
                "failures": self._failures,
                "opens": self.opens,
                "last_status": self._last_status,
            }

    def restore(self, state: dict, conservative: bool = False) -> None:
        with self._lock:
            open_until = float(state.get("open_until", 0) or 0)
            probe_until = float(state.get("probe_until", 0) or 0)
            failures = int(state.get("failures", 0) or 0)
            opens = int(state.get("opens", 0) or 0)
            if conservative:
                self._open_until = max(self._open_until, open_until, probe_until)
                self._failures = max(self._failures, failures)
                self.opens = max(self.opens, opens)
            else:
                self._open_until = max(open_until, probe_until)
                self._failures = failures
                self.opens = opens
            self._probe_in_flight = False
            self._probe_until = 0.0
            self._last_status = state.get("last_status")

    def stats(self) -> dict:
        with self._lock:
            return {
                "open": max(self._open_until, self._probe_until) > self._now(),
                "open_until": self._open_until,
                "probe_until": self._probe_until,
                "failures": self._failures,
                "opens": self.opens,
                "last_status": self._last_status,
            }


# ── CircuitStateStore（熔断状态主库，不存任何请求内容或凭据）──
class CircuitStateStore:
    """熔断状态 SQLite 主库，不存任何请求内容或凭据。"""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS circuit_state (
        group_name  TEXT PRIMARY KEY,
        open_until  REAL NOT NULL,
        probe_until REAL NOT NULL,
        failures    INTEGER NOT NULL,
        opens       INTEGER NOT NULL,
        last_status INTEGER,
        updated     REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS state_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        os.chmod(db_path, 0o600)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    def load_all(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT group_name, open_until, probe_until, failures, opens, "
                "last_status FROM circuit_state"
            ).fetchall()
        result = {}
        for group, open_until, probe_until, failures, opens, last_status in rows:
            if not isinstance(group, str):
                raise sqlite3.DatabaseError("invalid circuit group")
            if not isinstance(open_until, (int, float)) \
                    or not isinstance(probe_until, (int, float)):
                raise sqlite3.DatabaseError("invalid circuit deadline")
            if not isinstance(failures, int) or not isinstance(opens, int):
                raise sqlite3.DatabaseError("invalid circuit counters")
            if last_status is not None and not isinstance(last_status, int):
                raise sqlite3.DatabaseError("invalid circuit status")
            result[group] = {
                "open_until": open_until,
                "probe_until": probe_until,
                "failures": failures,
                "opens": opens,
                "last_status": last_status,
            }
        return result

    def save_all(self, states: dict[str, dict], now: float) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for group, state in states.items():
                    self._conn.execute(
                        "INSERT OR REPLACE INTO circuit_state "
                        "(group_name, open_until, probe_until, failures, opens, "
                        "last_status, updated) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (group, float(state.get("open_until", 0) or 0),
                         float(state.get("probe_until", 0) or 0),
                         int(state.get("failures", 0) or 0),
                         int(state.get("opens", 0) or 0),
                         state.get("last_status"), now),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def health_probe(self, now: float) -> None:
        marker = f"{now:.6f}"
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO state_meta (key, value) VALUES ('health', ?)",
                    (marker,),
                )
                row = self._conn.execute(
                    "SELECT value FROM state_meta WHERE key='health'"
                ).fetchone()
                if row != (marker,):
                    raise sqlite3.DatabaseError("state health probe readback mismatch")
                self._conn.execute("DELETE FROM state_meta WHERE key='health'")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ── CircuitStateManager（状态库安全闩）────────────────────────
class CircuitStateManager:
    """状态库安全闩：异常期间全局禁止受控来源出网。"""

    MARKER_VERSION = 1

    def __init__(self, state_dir: Path, groups: set[str], backoff=None,
                 now_fn=None, store_factory=None):
        self.state_dir = state_dir
        self.db_path = state_dir / "sgw_state.db"
        self.marker_path = state_dir / "sgw_safety_latch.json"
        self.groups = groups
        self.backoff = tuple(float(v) for v in (backoff or _DEFAULT_STATE_BACKOFF))
        if not self.backoff or any(value <= 0 for value in self.backoff):
            raise ValueError("state backoff schedule must contain positive values")
        self._now = now_fn or time.time
        self._store_factory = store_factory or CircuitStateStore
        self._lock = threading.Lock()
        self._probe_in_flight = False
        self.store: CircuitStateStore | None = None
        self.initial_states: dict[str, dict] = {}
        self._state = self._read_marker()
        marker_invalid = self._state is False

        candidate_store = None
        try:
            candidate_store = self._store_factory(self.db_path)
            db_states = candidate_store.load_all()
            self.store = candidate_store
        except Exception:
            if candidate_store is not None:
                try:
                    candidate_store.close()
                except Exception:
                    pass
            db_states = {}
            self.store = None

        marker_states = self._state.get("circuits", {}) if isinstance(self._state, dict) else {}
        self.initial_states = self._sanitize_states(
            self._merge_states(db_states, marker_states)
        )
        if isinstance(self._state, dict):
            self._state["circuits"] = self.initial_states

        if marker_invalid:
            # 单一介质异常从 10m 开始；主库与安全标记同时异常直接按 24h。
            stage = len(self.backoff) - 1 if self.store is None else 0
            self._state = self._waiting_state(stage, self.initial_states)
            self._write_marker_best_effort(self._state)
        elif self.store is None:
            if not isinstance(self._state, dict) or self._state.get("probe_state") == "recovered":
                self._state = self._waiting_state(0, self.initial_states)
            self._write_marker_best_effort(self._state)
        elif not isinstance(self._state, dict):
            recovered = self._recovered_state(self.initial_states)
            if self._write_marker_best_effort(recovered):
                self._state = recovered
            else:
                self._state = self._waiting_state(len(self.backoff) - 1, self.initial_states)
        elif self._state.get("probe_state") == "recovered":
            # 主库和上次安全标记都正常。
            pass

    @staticmethod
    def _merge_states(a: dict[str, dict], b: dict[str, dict]) -> dict[str, dict]:
        merged: dict[str, dict] = {}
        for group in set(a) | set(b):
            left, right = a.get(group, {}), b.get(group, {})
            merged[group] = {
                "open_until": max(float(left.get("open_until", 0) or 0),
                                  float(right.get("open_until", 0) or 0)),
                "probe_until": max(float(left.get("probe_until", 0) or 0),
                                   float(right.get("probe_until", 0) or 0)),
                "failures": max(int(left.get("failures", 0) or 0),
                                int(right.get("failures", 0) or 0)),
                "opens": max(int(left.get("opens", 0) or 0),
                             int(right.get("opens", 0) or 0)),
                "last_status": right.get("last_status", left.get("last_status")),
            }
        return merged

    def _sanitize_states(self, states: dict[str, dict]) -> dict[str, dict]:
        """只保留已配置组和数值熔断字段，阻断 URL/凭据误入状态文件。"""
        clean: dict[str, dict] = {}
        for group, state in states.items():
            if group not in self.groups or not isinstance(state, dict):
                continue
            try:
                last_status = state.get("last_status")
                if last_status is not None and not isinstance(last_status, int):
                    last_status = None
                clean[group] = {
                    "open_until": float(state.get("open_until", 0) or 0),
                    "probe_until": float(state.get("probe_until", 0) or 0),
                    "failures": int(state.get("failures", 0) or 0),
                    "opens": int(state.get("opens", 0) or 0),
                    "last_status": last_status,
                }
            except (TypeError, ValueError):
                continue
        return clean

    def _read_marker(self) -> dict | bool | None:
        if not self.marker_path.exists():
            return None
        try:
            data = json.loads(self.marker_path.read_text(encoding="utf-8"))
            required = {"version", "probe_state", "backoff_stage", "next_probe_at", "circuits"}
            if not isinstance(data, dict) or not required.issubset(data):
                return False
            if data["version"] != self.MARKER_VERSION:
                return False
            if data["probe_state"] not in {"waiting", "recovered"}:
                return False
            stage = data["backoff_stage"]
            if not isinstance(stage, int) or not 0 <= stage < len(self.backoff):
                return False
            if not isinstance(data["next_probe_at"], (int, float)):
                return False
            if not isinstance(data["circuits"], dict):
                return False
            for state in data["circuits"].values():
                if not isinstance(state, dict):
                    return False
                if not isinstance(state.get("open_until", 0), (int, float)):
                    return False
                if not isinstance(state.get("probe_until", 0), (int, float)):
                    return False
                if not isinstance(state.get("failures", 0), int):
                    return False
                if not isinstance(state.get("opens", 0), int):
                    return False
                if state.get("last_status") is not None \
                        and not isinstance(state["last_status"], int):
                    return False
            return data
        except Exception:
            return False

    def _write_marker(self, data: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.marker_path.with_name(
            f".{self.marker_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.chmod(tmp, 0o600)
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(fd)
        os.replace(tmp, self.marker_path)
        os.chmod(self.marker_path, 0o600)
        dir_fd = os.open(self.state_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def _write_marker_best_effort(self, data: dict) -> bool:
        try:
            self._write_marker(data)
            return True
        except Exception:
            return False

    def _waiting_state(self, stage: int, circuits: dict[str, dict],
                       first_failure_at: float | None = None) -> dict:
        now = self._now()
        stage = max(0, min(stage, len(self.backoff) - 1))
        return {
            "version": self.MARKER_VERSION,
            "probe_state": "waiting",
            "first_failure_at": now if first_failure_at is None else first_failure_at,
            "last_failure_at": now,
            "last_success_at": None,
            "backoff_stage": stage,
            "next_probe_at": now + self.backoff[stage],
            "circuits": circuits,
        }

    def _recovered_state(self, circuits: dict[str, dict]) -> dict:
        now = self._now()
        return {
            "version": self.MARKER_VERSION,
            "probe_state": "recovered",
            "first_failure_at": None,
            "last_failure_at": None,
            "last_success_at": now,
            "backoff_stage": 0,
            "next_probe_at": 0,
            "circuits": circuits,
        }

    def save_all(self, states: dict[str, dict]) -> bool:
        """先写保护标记，再事务写主库，最后标记恢复。"""
        states = self._sanitize_states(states)
        now = self._now()
        guard = self._waiting_state(0, states)
        with self._lock:
            if not self.ready:
                return False
            if not self._write_marker_best_effort(guard):
                self._state = self._waiting_state(len(self.backoff) - 1, states)
                return False
            self._state = guard
            try:
                assert self.store is not None
                self.store.save_all(states, now)
                recovered = self._recovered_state(states)
                if not self._write_marker_best_effort(recovered):
                    return False
                self._state = recovered
                return True
            except Exception:
                return False

    @property
    def ready(self) -> bool:
        return (
            self.store is not None
            and isinstance(self._state, dict)
            and self._state.get("probe_state") == "recovered"
        )

    def before_egress(self) -> tuple[bool, dict[str, dict] | None]:
        """必要时只执行一次存储健康探测，绝不访问上游。"""
        with self._lock:
            if self.ready:
                return True, None
            now = self._now()
            if now < float(self._state.get("next_probe_at", float("inf"))):
                return False, None
            if self._probe_in_flight:
                return False, None
            self._probe_in_flight = True

        store = self.store
        created_store = False
        try:
            if store is None:
                store = self._store_factory(self.db_path)
                created_store = True
            store.health_probe(self._now())
            states = self._sanitize_states(
                self._merge_states(store.load_all(), self._state.get("circuits", {}))
            )
            recovered = self._recovered_state(states)
            if not self._write_marker_best_effort(recovered):
                raise OSError("cannot persist recovered safety marker")
            with self._lock:
                self.store = store
                self._state = recovered
            return True, states
        except Exception:
            if created_store and store is not None:
                try:
                    store.close()
                except Exception:
                    pass
            with self._lock:
                stage = min(int(self._state.get("backoff_stage", 0)) + 1,
                            len(self.backoff) - 1)
                first = self._state.get("first_failure_at")
                self._state = self._waiting_state(
                    stage, self._state.get("circuits", {}), first
                )
                self._write_marker_best_effort(self._state)
            return False, None
        finally:
            with self._lock:
                self._probe_in_flight = False

    def stats(self) -> dict:
        with self._lock:
            state = dict(self._state) if isinstance(self._state, dict) else {}
            state.pop("circuits", None)
            state["ready"] = self.ready
            return state

    def close(self) -> None:
        if self.store is not None:
            self.store.close()
            self.store = None


# ── 令牌桶：按域名组，全局串行限流 ─────────────────────────────
class TokenBucket:
    """简单的最小间隔限流器（对齐上游 EM_MIN_INTERVAL 语义）。

    非传统令牌桶，而是「两次请求间至少间隔 1/rps 秒 + jitter」。
    全局串行：无论多少并发到达，按 acquire 顺序排队等待。
    """

    def __init__(self, rps: float, jitter: tuple[float, float]):
        self.min_interval = 1.0 / rps if rps > 0 else 0
        self.jitter = jitter
        self._last = 0.0
        self._lock = threading.Lock()
        self.wait_count = 0

    def acquire(self) -> float:
        """阻塞直到允许下一次请求，返回实际等待秒数。

        sleep 在锁外执行：锁内只算出该请求被允许的时刻并预占（下次时间戳），
        这样并发请求各自拿到自己的等待时长后并行睡眠，串行化的是「外网出口时刻」。
        """
        with self._lock:
            now = time.time()
            wait = self.min_interval - (now - self._last)
            if wait > 0:
                jitter = random.uniform(*self.jitter)
                self._last = self._last + self.min_interval + jitter  # 排队预占
                self.wait_count += 1
                total = wait + jitter
            else:
                self._last = now
                total = 0
        if total > 0:
            time.sleep(total)
        return total
