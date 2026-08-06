#!/usr/bin/env python3
"""sgw_proxy — A股数据共享流量网关。

单进程 HTTP 代理，供单 IP 下 100~1000 个 agent 并发共享：
  - 按域名组令牌桶限流（东财组/同花顺组，全局串行，跨进程生效）
  - 五档缓存（P/L/S/R/N，TTL 由调用方用 X-Cache-Tier 头声明）
  - 403/429 立即熔断，5xx/异常按总预算重试
  - 响应指纹日志（供离线修正分档规则）
  - GET /__stats 暴露计数器

设计依据：.agents/notes/gateway-design.md
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import random
import signal
import sqlite3
import threading
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "config.toml"

# 风控源走网关。腾讯/新浪/财联经资料核实均有 IP 风控(data-source-risk-control.md)，
# .hexin.cn 与 .10jqka.com.cn 同属同花顺系、共用 hexin-v 风控，故一并经网关。
# 注：百度(.baidu.com)需网关支持 curl_cffi 指纹出网，单列处理；mootdx 走 TCP 单列。
PROXIED_DOMAIN_SUFFIXES = (
    ".eastmoney.com", ".10jqka.com.cn", ".hexin.cn",
    ".gtimg.cn",             # 腾讯 qt.gtimg.cn 实时行情
    ".sina.cn", ".sinajs.cn",  # 新浪行情/财报/期权
    ".cls.cn",               # 财联社电报
    ".cninfo.com.cn",        # 巨潮公告/互动易
    ".legulegu.com",         # 理杏仁估值历史
    ".baidu.com",            # 百度股市通(需 curl_cffi 指纹)
)

# 允许从客户端透传到上游的请求头白名单（hop-by-hop/敏感头一律不透传）。
# 覆盖深交所 Referer、同花顺 hexin-v(Cookie)、乐咕 CSRF、东财 Accept 等需求。
# Host/Connection/Authorization/Content-Length 等不在白名单，禁止透传。
UPSTREAM_HEADER_ALLOWLIST = {
    "User-Agent", "Referer", "Cookie", "X-CSRF-Token", "Accept", "Origin",
}

# 经端点策略确认会改变公共响应表示的头。Cookie/CSRF/Referer 只是访问门票，
# 不得进入共享缓存身份，否则会泄漏凭据并让 100~1000 agent 的缓存碎片化。
PUBLIC_RESPONSE_AFFECTING_HEADERS = {"Accept"}

_REVIEW_STATUSES = {"approved", "blocked", "unknown"}
_IP_RISKS = {"controlled", "safe"}
_RESPONSE_SCOPES = {"public", "credential_bound", "user_private"}
_CREDENTIAL_MODES = {"none", "gateway_session", "caller"}
_CACHE_MODES = {"shared", "isolated", "disabled"}
_DEFAULT_STATE_BACKOFF = (600, 1800, 3600, 21600, 43200, 86400)

# 固定 UA（客户端未传 User-Agent 时的默认）
_DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def _canonical_url(url: str, params: dict | None = None,
                   header_key_parts: dict | None = None,
                   ignored_params: set[str] | None = None) -> str:
    """规范化 URL：合并 target_url 自带 query 与 params，按 key 排序，统一编码。

    用于 cache key：确保 (url, {a:1,b:2}) 与 (url, {b:2,a:1}) 与
    (url?a=1, {b:2}) 产生同一 key，且不同 params 产生不同 key。

    header_key_parts：经策略确认影响响应表示的非敏感请求头。
    ignored_params：只用于访问控制、不影响公共响应的敏感 query 参数；这些参数
    仍会发给上游，但不会进入缓存键、SQLite 或指纹日志。
    """
    parsed = urlparse(url)
    # 合并 url 自带 query 与 params（params 优先，覆盖同名）
    merged: dict[str, list[str]] = parse_qs(parsed.query, keep_blank_values=True)
    if params:
        for k, v in params.items():
            merged[k] = [str(v)]
    for key in ignored_params or ():
        merged.pop(key, None)
    # 影响响应的请求头作为附属 key 片段（前缀 __h_ 避免与真实 query 冲突）
    if header_key_parts:
        for k, v in header_key_parts.items():
            merged[f"__h_{k.lower()}"] = [str(v)]
    # 规范 query：按 key 排序，标准 urlencoding
    sorted_query = urlencode(sorted(merged.items()), doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", sorted_query, ""))


@dataclass(frozen=True)
class EndpointPolicy:
    """一个上游端点的正交策略；未匹配端点默认拒绝。"""

    name: str
    host: str
    path: str
    review_status: str
    ip_risk: str
    response_scope: str
    credential_mode: str
    cache_mode: str
    credential_params: frozenset[str]
    identity_ignored_params: frozenset[str]
    egress_client: str  # "requests"(默认) / "curl_cffi"(带 Chrome TLS 指纹出网)

    @classmethod
    def from_config(cls, raw: dict) -> "EndpointPolicy":
        required = {
            "name", "host", "path", "review_status", "ip_risk",
            "response_scope", "credential_mode", "cache_mode",
        }
        missing = required - raw.keys()
        if missing:
            raise ValueError(f"endpoint policy missing fields: {sorted(missing)}")
        policy = cls(
            name=raw["name"], host=raw["host"].lower(), path=raw["path"],
            review_status=raw["review_status"], ip_risk=raw["ip_risk"],
            response_scope=raw["response_scope"],
            credential_mode=raw["credential_mode"], cache_mode=raw["cache_mode"],
            credential_params=frozenset(raw.get("credential_params", [])),
            identity_ignored_params=frozenset(raw.get("identity_ignored_params", [])),
            egress_client=raw.get("egress_client", "requests"),
        )
        policy.validate()
        return policy

    def validate(self) -> None:
        checks = (
            (self.review_status, _REVIEW_STATUSES, "review_status"),
            (self.ip_risk, _IP_RISKS, "ip_risk"),
            (self.response_scope, _RESPONSE_SCOPES, "response_scope"),
            (self.credential_mode, _CREDENTIAL_MODES, "credential_mode"),
            (self.cache_mode, _CACHE_MODES, "cache_mode"),
        )
        for value, allowed, field in checks:
            if value not in allowed:
                raise ValueError(f"endpoint {self.name}: invalid {field}={value!r}")
        if self.response_scope == "public" and self.cache_mode != "shared":
            raise ValueError(f"endpoint {self.name}: public response must use shared cache")
        if self.response_scope == "credential_bound" and self.cache_mode != "isolated":
            raise ValueError(f"endpoint {self.name}: credential_bound response must be isolated")
        if self.response_scope == "user_private" and self.cache_mode != "disabled":
            raise ValueError(f"endpoint {self.name}: user_private response must disable cache")

    def matches(self, host: str, path: str) -> bool:
        return host.lower() == self.host and fnmatch.fnmatchcase(path, self.path)


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


def _filtered_client_headers(client_headers: dict | None) -> dict:
    """从客户端请求头提取白名单内的透传头（值规范化去空）。

    返回 {header: value}（header 名规范化为白名单中的形式）；
    User-Agent 缺失时填默认。大小写不敏感匹配（HTTP 头名大小写无关）。
    """
    if not client_headers:
        return {"User-Agent": _DEFAULT_UA}
    # 构建小写名 → 原始名 的白名单查找表，实现大小写不敏感匹配
    allow_lower = {h.lower(): h for h in UPSTREAM_HEADER_ALLOWLIST}
    out: dict[str, str] = {}
    for name, value in client_headers.items():
        canonical = allow_lower.get(name.lower())
        if canonical and value:
            out[canonical] = value
    out.setdefault("User-Agent", _DEFAULT_UA)
    return out


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


# ── 缓存：内存，按 key 存 (resp_bytes, headers, expire_ts, tier) ──
class Cache:
    def __init__(self):
        self._store: dict[str, tuple[bytes, dict, float, str]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[tuple[bytes, dict]]:
        with self._lock:
            entry = self._store.get(key)
            if entry and entry[2] > time.time():
                self.hits += 1
                return entry[0], entry[1]
            if entry:
                del self._store[key]  # 过期
            self.misses += 1
            return None

    def set(self, key: str, body: bytes, headers: dict, ttl: int, tier: str):
        if ttl <= 0:
            return
        with self._lock:
            self._store[key] = (body, headers, time.time() + ttl, tier)

    def stats(self) -> dict:
        with self._lock:
            return {"size": len(self._store), "hits": self.hits, "misses": self.misses}


# ── 磁盘缓存：SQLite + WAL，仅持久化 P/L 档（§3.4.8）─────────────
class DiskCache:
    """P/L 档缓存的磁盘持久层。

    write-through：每次 set 同步落盘；get 读盘回填内存。重启后 load_all 回填。
    WAL 模式读不阻塞写；写用一把锁串行化（P/L 写入 ≤1 req/s，无竞争压力）。
    过期清理：启动 load_all 扫表删过期 + get 命中过期惰性删除，无后台线程。
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS cache (
        key     TEXT PRIMARY KEY,
        body    BLOB,
        headers TEXT,
        expire  REAL,
        tier    TEXT,
        created REAL
    )
    """

    def __init__(self, db_path: Path, tiers: set[str]):
        self.db_path = db_path
        self.tiers = tiers
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[tuple[bytes, dict]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT body, headers, expire FROM cache WHERE key=?", (key,)
            ).fetchone()
        if not row:
            self.misses += 1
            return None
        body, headers_json, expire = row
        if expire <= time.time():
            # 惰性删除过期项
            with self._lock:
                self._conn.execute("DELETE FROM cache WHERE key=?", (key,))
                self._conn.commit()
            self.misses += 1
            return None
        self.hits += 1
        return body, json.loads(headers_json)

    def set(self, key: str, body: bytes, headers: dict, ttl: int, tier: str):
        if tier not in self.tiers:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (key, body, headers, expire, tier, created) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, body, json.dumps(headers, ensure_ascii=False),
                 time.time() + ttl, tier, time.time()),
            )
            self._conn.commit()

    def load_all(self) -> dict[str, tuple[bytes, dict, float, str]]:
        """启动时回填内存。过滤并删除过期项，返回未过期的 {key: (body, headers, expire, tier)}。"""
        now = time.time()
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, body, headers, expire, tier FROM cache"
            ).fetchall()
            # 删除所有过期项
            self._conn.execute("DELETE FROM cache WHERE expire <= ?", (now,))
            self._conn.commit()
        result = {}
        for key, body, headers_json, expire, tier in rows:
            if expire > now:
                result[key] = (body, json.loads(headers_json), expire, tier)
        return result

    def stats(self) -> dict:
        with self._lock:
            size = self._conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        return {"size": size, "hits": self.hits, "misses": self.misses}

    def close(self):
        with self._lock:
            self._conn.close()


# ── 网关主体 ──────────────────────────────────────────────────
class Gateway:
    def __init__(self, config: dict, fp_dir_override: str | None = None,
                 cache_dir_override: str | None = None,
                 state_dir_override: str | None = None):
        self.cfg = config
        self._closed = False
        self._fp_dir_override = fp_dir_override
        self._cache_dir_override = cache_dir_override
        self._state_dir_override = state_dir_override
        # 域名 -> 组名
        self.domain_group: dict[str, str] = {}
        self.buckets: dict[str, TokenBucket] = {}
        for g in config.get("group", []):
            name = g["name"]
            jitter = tuple(g.get("jitter", [0, 0]))
            self.buckets[name] = TokenBucket(g["rps"], jitter)
            for d in g["domains"]:
                self.domain_group[d] = name
        self.endpoint_policies = [
            EndpointPolicy.from_config(raw) for raw in config.get("endpoint", [])
        ]
        if not self.endpoint_policies:
            raise ValueError("no endpoint policies configured; fail closed")
        policy_keys = [(p.host, p.path) for p in self.endpoint_policies]
        if len(policy_keys) != len(set(policy_keys)):
            raise ValueError("duplicate endpoint policy host/path")
        circuit_cfg = config.get("circuit", {})
        retry_cfg = config.get("retry", {})
        self.max_attempts = max(1, int(retry_cfg.get("max_attempts", 3)))
        state_cfg = config.get("state", {})
        self.state_manager: CircuitStateManager | None = None
        initial_circuit_states: dict[str, dict] = {}
        if state_cfg.get("enabled", False):
            state_dir = Path(self._state_dir_override or state_cfg.get("dir", "state"))
            if not state_dir.is_absolute():
                state_dir = HERE / state_dir
            self.state_manager = CircuitStateManager(
                state_dir,
                set(self.buckets),
                backoff=state_cfg.get("backoff_seconds", _DEFAULT_STATE_BACKOFF),
            )
            initial_circuit_states = self.state_manager.initial_states
        self.circuits = {
            name: CircuitBreaker(
                cooldown=float(circuit_cfg.get("cooldown_seconds", 300)),
                failure_threshold=int(circuit_cfg.get("failure_threshold", 3)),
                probe_lease=float(circuit_cfg.get("probe_lease_seconds", 120)),
            )
            for name in self.buckets
        }
        for name, state in initial_circuit_states.items():
            if name in self.circuits:
                self.circuits[name].restore(state)
        for circuit in self.circuits.values():
            circuit._on_change = self._persist_circuit_states
        self.singleflight = SingleFlight()
        self.cache = Cache()
        # 每组计数
        self.group_reqs: dict[str, int] = {n: 0 for n in self.buckets}
        self.group_errs: dict[str, int] = {n: 0 for n in self.buckets}
        # 磁盘缓存（P/L 档持久化，§3.4.8）
        self.disk_cache: Optional[DiskCache] = None
        self._disk_load_count = 0
        self._disk_load_ms = 0
        persist = config.get("cache", {}).get("persist", {})
        if persist.get("enabled", False):
            cache_dir = Path(self._cache_dir_override or persist.get("dir", "cache"))
            if not cache_dir.is_absolute():
                cache_dir = HERE / cache_dir
            tiers = set(persist.get("tiers", ["P", "L"]))
            self.disk_cache = DiskCache(cache_dir / "sgw_cache.db", tiers)
            t0 = time.time()
            for key, (body, headers, expire, tier) in self.disk_cache.load_all().items():
                self.cache._store[key] = (body, headers, expire, tier)
            self._disk_load_count = len(self.cache._store)
            self._disk_load_ms = round((time.time() - t0) * 1000, 1)
        # 指纹日志
        fp = config.get("fingerprint", {})
        self.fp_enabled = fp.get("enabled", False)
        self.fp_strip = set(fp.get("strip_fields", []))
        # 日志目录：--fp-dir > config log_dir > 默认 包目录/logs
        self.fp_dir = Path(self._fp_dir_override or fp.get("log_dir", "logs"))
        if not self.fp_dir.is_absolute():
            self.fp_dir = HERE / self.fp_dir
        self.fp_last_hash: dict[str, str] = {}  # key -> 上次 resp_hash
        self.fp_lock = threading.Lock()

    def _circuit_snapshots(self) -> dict[str, dict]:
        return {name: circuit.snapshot() for name, circuit in self.circuits.items()}

    def _persist_circuit_states(self) -> bool:
        if self.state_manager is None:
            return True
        return self.state_manager.save_all(self._circuit_snapshots())

    def _restore_circuit_states(self, states: dict[str, dict]) -> None:
        for name, state in states.items():
            if name in self.circuits:
                self.circuits[name].restore(state, conservative=True)

    # ── 域名归组 ──
    def group_of(self, host: str) -> Optional[str]:
        # 第一层：风控源后缀准入（东财/同花顺）+ exact-host 归组
        for suffix in PROXIED_DOMAIN_SUFFIXES:
            if host.endswith(suffix):
                return self.domain_group.get(host)
        # 第二层：非后缀源（如交易所 www.szse.cn）按 config exact-host 归组
        return self.domain_group.get(host)

    def policy_for(self, host: str, path: str) -> Optional[EndpointPolicy]:
        """返回唯一匹配策略；配置歧义视为启动/请求错误，不猜测。"""
        matches = [p for p in self.endpoint_policies if p.matches(host, path)]
        if len(matches) > 1:
            raise ValueError(f"ambiguous endpoint policy for {host}{path}")
        return matches[0] if matches else None

    # ── 档位 → TTL（先验方案 §3.4.6）──
    def ttl_for_tier(self, tier: str) -> int:
        c = self.cfg["cache"]
        if tier == "P":
            return c["P_ttl"]
        if tier == "L":
            return c["L_ttl"]
        if tier == "S":
            return c["S_ttl_afterclose"] if not self._is_intraday() else c["S_ttl_session"]
        # R / N / 未知 → no-cache
        return 0

    def _is_intraday(self) -> bool:
        """简化交易时段判断：工作日 09:00-18:00。MVP，P4 校准。"""
        now = datetime.now()
        if now.weekday() >= 5:  # 周六日
            return False
        s = self.cfg["cache"]["session"]
        t = now.strftime("%H:%M")
        return s["intraday_start"] <= t < s["intraday_end"]

    # ── 兜底档位（裸请求，§3.4.6）──
    def fallback_tier(self, path: str) -> str:
        fb = self.cfg.get("fallback", {})
        for rule in fb.get("rules", []):
            if rule["path_contains"] in path:
                return rule["tier"]
        return fb.get("default_tier", "R")

    # ── 响应哈希（剔除动态字段，§3.4.7）──
    def _resp_fingerprint(self, body: bytes) -> str:
        """对响应体算哈希，剔除已知动态字段。"""
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                for f in self.fp_strip:
                    data.pop(f, None)
                    # 嵌套一层（东财常 result.data 包裹）
                    if isinstance(data.get("result"), dict):
                        data["result"].pop(f, None)
                body = json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
        except Exception:
            pass  # 非 JSON（如 PDF），直接哈希原始字节
        return hashlib.md5(body).hexdigest()

    def _log_fingerprint(self, key: str, tier: str, body: bytes, session: str):
        if not self.fp_enabled or not self.fp_dir:
            return
        h = self._resp_fingerprint(body)
        with self.fp_lock:
            prev = self.fp_last_hash.get(key)
            changed = prev != h
            self.fp_last_hash[key] = h
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "key": key, "tier": tier, "session": session,
            "resp_hash": h[:12], "changed": changed,
        }
        # 按天拆分：sgw_fp_YYYYMMDD.jsonl，避免超大文件
        self.fp_dir.mkdir(parents=True, exist_ok=True)
        fp_file = self.fp_dir / f"sgw_fp_{datetime.now().strftime('%Y%m%d')}.jsonl"
        with open(fp_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ── 核心请求处理 ──
    def handle(self, target_url: str, params: dict, tier_header: Optional[str],
               client_headers: dict | None = None, *,
               method: str = "GET", body: dict | None = None,
               body_type: str = "json") -> tuple[int, bytes, dict]:
        parsed = urlparse(target_url)
        host = parsed.hostname or ""
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            return 400, b'{"error":"invalid upstream URL"}', {}
        if parsed.port not in (None, 80, 443):
            return 400, b'{"error":"upstream port is not allowed"}', {}
        group = self.group_of(host)
        if group is None:
            return 400, b'{"error":"domain not allowed by gateway"}', {}

        policy = self.policy_for(host, parsed.path)
        if policy is None or policy.review_status != "approved":
            return 403, b'{"error":"endpoint policy is unknown or blocked"}', {}
        if policy.ip_risk != "controlled":
            return 400, b'{"error":"endpoint policy does not permit gateway egress"}', {}
        # 公共网关不承载私有响应；否则一次误配置就可能跨 agent 泄漏。
        if policy.response_scope == "user_private":
            return 403, b'{"error":"private endpoint requires isolated gateway"}', {}

        # 档位：头声明优先，否则兜底
        tier = tier_header if tier_header in ("P", "L", "S", "R", "N") else self.fallback_tier(parsed.path)
        ttl = self.ttl_for_tier(tier)
        if policy.cache_mode == "disabled":
            ttl = 0

        # 透传给上游的白名单请求头
        fwd_headers = _filtered_client_headers(client_headers)
        if policy.credential_mode == "none":
            fwd_headers.pop("Cookie", None)
            fwd_headers.pop("X-CSRF-Token", None)
        elif policy.credential_mode == "gateway_session":
            # 网关会话尚未配置时失败关闭，绝不借用任意 caller 凭据。
            fwd_headers.pop("Cookie", None)
            fwd_headers.pop("X-CSRF-Token", None)
            return 503, b'{"error":"gateway credential session unavailable"}', {}

        header_key_parts = {
            k: v for k, v in fwd_headers.items()
            if k in PUBLIC_RESPONSE_AFFECTING_HEADERS
        }

        if policy.response_scope == "credential_bound":
            identity = (client_headers or {}).get("X-SGW-Identity")
            if not identity:
                return 403, b'{"error":"credential-bound endpoint requires identity"}', {}
            # 只使用调用方提供的非秘密身份 ID；Cookie/CSRF 本身永不进入 key。
            header_key_parts["X-SGW-Identity"] = identity

        # 缓存 key = tier + canonical URL（含合并后的 query + 影响响应的头）
        # 必须含 params，否则不同股票/日期/页码会串缓存
        ignored_params = set(policy.credential_params | policy.identity_ignored_params)
        cache_key = f"{tier}|{_canonical_url(target_url, params, header_key_parts, ignored_params)}"
        # POST body 必须进入 key：否则不同 body（不同 top/不同股票）会串缓存。
        # 用稳定的 json 排序哈希，避免 body 字段顺序差异制造缓存碎片。
        if method == "POST" and body:
            body_hash = hashlib.md5(
                json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            cache_key = f"{cache_key}|body={body_hash}"
        cached = self.cache.get(cache_key) if ttl > 0 else None
        if cached:
            body, headers = cached
            self._log_fingerprint(cache_key, tier, body, "cache_hit")
            return 200, body, {**headers, "X-Cache": "HIT-MEM", "X-Cache-Tier": tier}
        # 内存未命中：回查磁盘缓存（仅 P/L 持久化档）
        if ttl > 0 and self.disk_cache is not None:
            disk = self.disk_cache.get(cache_key)
            if disk:
                body, headers = disk
                # 回填内存，后续命中走内存
                self.cache.set(cache_key, body, headers, ttl, tier)
                self._log_fingerprint(cache_key, tier, body, "cache_hit_disk")
                return 200, body, {**headers, "X-Cache": "HIT-DISK", "X-Cache-Tier": tier}

        # 即使 TTL=0，也合并同一时刻的相同请求，避免实时端点冷 miss 风暴。
        leader, flight = self.singleflight.join(cache_key)
        if not leader:
            if not flight.event.wait(timeout=30):
                return 504, b'{"error":"coalesced upstream request timed out"}', {"X-Cache-Tier": tier}
            assert flight.result is not None
            status, body, headers = flight.result
            return status, body, {**headers, "X-Cache": "COALESCED"}

        result: tuple[int, bytes, dict]
        try:
            result = self._fetch_upstream(
                target_url, params, fwd_headers, policy, group, tier, ttl, cache_key,
                method=method, body=body, body_type=body_type,
            )
        except Exception:
            result = (502, b'{"error":"gateway internal upstream failure"}', {"X-Cache-Tier": tier})
        self.singleflight.finish(cache_key, flight, result)
        return result

    def _egress_request(self, method: str, policy: EndpointPolicy,
                        url: str, **kwargs) -> "requests.Response":
        """按 endpoint 的 egress_client 选出网客户端。

        - requests(默认)：标准 requests，适用于绝大多数源。用 .get/.post
          （非 .request）以保持与现有测试 mock 点(sgw.proxy.requests.get/post)一致。
        - curl_cffi：带 Chrome TLS 指纹(impersonate=chrome)，用于有协议栈风控的源(百度)。
          curl_cffi 的 RequestsError 继承 requests.RequestException，异常处理兼容。
        """
        kwargs.setdefault("timeout", 15)
        if policy.egress_client == "curl_cffi":
            from curl_cffi import requests as curl_requests
            kwargs["impersonate"] = "chrome"
            return curl_requests.request(method, url, **kwargs)
        # requests 路径用具名方法（get/post），便于测试 mock 且语义清晰
        fn = requests.get if method == "get" else requests.post
        return fn(url, **kwargs)

    def _fetch_upstream(self, target_url: str, params: dict, fwd_headers: dict,
                        policy: EndpointPolicy, group: str, tier: str, ttl: int,
                        cache_key: str, *, method: str = "GET",
                        body: dict | None = None,
                        body_type: str = "json") -> tuple[int, bytes, dict]:
        if self.state_manager is not None:
            allowed, recovered_states = self.state_manager.before_egress()
            if recovered_states:
                self._restore_circuit_states(recovered_states)
            if not allowed:
                return 503, b'{"error":"state store safety latch open; cache only"}', {
                    "X-Cache-Tier": tier
                }
        circuit = self.circuits[group]
        if circuit.is_open():
            return 503, b'{"error":"source circuit open; cache only"}', {"X-Cache-Tier": tier}

        # 构造一次规范 URL，确保真实请求与缓存身份对重复 query 参数的处理一致。
        request_url = _canonical_url(target_url, params)
        last_err = None
        for attempt in range(self.max_attempts):
            # 每次实际出网（包括重试）都必须重新经过全局限流。
            self.buckets[group].acquire()
            if not circuit.before_request():
                return 503, b'{"error":"source circuit open; cache only"}', {"X-Cache-Tier": tier}
            self.group_reqs[group] += 1
            try:
                if method == "POST" and body is not None:
                    if body_type == "form":
                        # form-encoded POST（巨潮公告/互动易等）
                        r = self._egress_request("post", policy, request_url,
                                                 data=body, headers=fwd_headers)
                    else:
                        # JSON POST（东财人气榜/概念等）
                        r = self._egress_request("post", policy, request_url,
                                                 json=body, headers=fwd_headers)
                else:
                    r = self._egress_request("get", policy, request_url, headers=fwd_headers)
                if r.status_code in (403, 429):
                    # 家庭 IP 不可更换：首次风控信号立即全来源熔断且不重试。
                    circuit.failure(immediate=True, status=r.status_code)
                    self.group_errs[group] += 1
                    return r.status_code, b'{"error":"source blocked; circuit opened"}', {"X-Cache-Tier": tier}
                if r.status_code in (500, 502, 503, 504):
                    circuit.failure(status=r.status_code)
                    last_err = r.status_code
                    if circuit.is_open() or attempt == self.max_attempts - 1:
                        break
                    time.sleep(0.6 * (2 ** attempt))
                    continue
                # 成功
                circuit.success()
                resp_headers = {"Content-Type": r.headers.get("Content-Type", "application/json")}
                if ttl > 0:
                    self.cache.set(cache_key, r.content, resp_headers, ttl, tier)
                    # write-through：P/L 档同步落盘（§3.4.8）
                    if self.disk_cache is not None:
                        self.disk_cache.set(cache_key, r.content, resp_headers, ttl, tier)
                session = "intraday" if self._is_intraday() else "afterclose"
                self._log_fingerprint(cache_key, tier, r.content, session)
                return r.status_code, r.content, {**resp_headers, "X-Cache": "MISS", "X-Cache-Tier": tier}
            except requests.RequestException as e:
                circuit.failure()
                last_err = str(e)
                if attempt < self.max_attempts - 1 and not circuit.is_open():
                    time.sleep(0.6 * (2 ** attempt))
                    continue
                break
        self.group_errs[group] += 1
        # 不把异常文本原样返回，避免 requests 把带敏感 query 的 URL 写入响应/日志。
        reason = last_err if isinstance(last_err, int) else "request error"
        return 502, json.dumps({"error": "upstream failed", "reason": reason}).encode(), {"X-Cache-Tier": tier}

    def stats(self) -> dict:
        return {
            "group_reqs": self.group_reqs,
            "group_errs": self.group_errs,
            "bucket_waits": {n: b.wait_count for n, b in self.buckets.items()},
            "singleflight_followers": self.singleflight.followers,
            "circuits": {n: c.stats() for n, c in self.circuits.items()},
            "state_safety": self.state_manager.stats() if self.state_manager else None,
            "cache": self.cache.stats(),
            "disk_cache": self.disk_cache.stats() if self.disk_cache else None,
            "disk_load_count": self._disk_load_count,
            "disk_load_ms": self._disk_load_ms,
            "intraday": self._is_intraday(),
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.disk_cache:
            self.disk_cache.close()
        if self.state_manager:
            self.state_manager.close()


# ── HTTP handler ──────────────────────────────────────────────
def make_handler(gateway: Gateway):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # 静默默认日志

        def do_GET(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)

            # /__stats 观测端点
            if parsed.path == "/__stats":
                body = json.dumps(gateway.stats(), ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            # 透明代理：u=原始URL，其余 query 作为 params 转发
            target_url = qs.get("u", [None])[0]
            if not target_url:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"missing ?u=<url>"}')
                return
            # u 之后的参数都是要转发给上游的
            params = {k: v[0] for k, v in qs.items() if k != "u"}
            tier = self.headers.get("X-Cache-Tier")
            # 客户端请求头（白名单内的会透传到上游）
            client_headers = {k: v for k, v in self.headers.items()}

            status, body, headers = gateway.handle(target_url, params, tier, client_headers)
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)

            # POST 透明代理：u=原始URL，query 参数仍走 ?k=v，body 在请求体
            target_url = qs.get("u", [None])[0]
            if not target_url:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"missing ?u=<url>"}')
                return
            params = {k: v[0] for k, v in qs.items() if k != "u"}
            tier = self.headers.get("X-Cache-Tier")
            client_headers = {k: v for k, v in self.headers.items()}
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            ctype = (self.headers.get("Content-Type") or "").lower()

            if "application/x-www-form-urlencoded" in ctype:
                # form-encoded POST（巨潮公告/互动易等）：parse_qs 解析成 dict
                post_body = {k: v[-1] for k, v in parse_qs(raw.decode("utf-8")).items()} if raw else {}
                status, resp_body, headers = gateway.handle(
                    target_url, params, tier, client_headers,
                    method="POST", body=post_body, body_type="form",
                )
            else:
                # JSON POST（东财人气榜/概念等）
                try:
                    post_body = json.loads(raw) if raw else None
                except (ValueError, json.JSONDecodeError):
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"POST body must be valid JSON"}')
                    return
                if post_body is not None and not isinstance(post_body, dict):
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"POST body must be a JSON object"}')
                    return
                status, resp_body, headers = gateway.handle(
                    target_url, params, tier, client_headers,
                    method="POST", body=post_body, body_type="json",
                )

            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)

    return Handler


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def main():
    ap = argparse.ArgumentParser(description="sgw_proxy - A股数据共享流量网关")
    ap.add_argument("-c", "--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--fp-dir", default=None,
                    help="指纹日志目录（生产环境必须指定，如 /var/log/sgw）")
    ap.add_argument("--cache-dir", default=None,
                    help="磁盘缓存目录（P/L 档持久化，生产建议 /var/lib/sgw）")
    ap.add_argument("--state-dir", default=None,
                    help="熔断状态与安全标记目录（生产建议 /var/lib/sgw/state）")
    ap.add_argument("--max-attempts", type=int, default=None,
                    help="覆盖上游最大尝试次数；真实 canary 必须设为 1")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    if args.max_attempts is not None:
        cfg.setdefault("retry", {})["max_attempts"] = max(1, args.max_attempts)
    host = args.host or cfg["server"]["host"]
    port = args.port or cfg["server"]["port"]

    gateway = Gateway(
        cfg,
        fp_dir_override=args.fp_dir,
        cache_dir_override=args.cache_dir,
        state_dir_override=args.state_dir,
    )
    server = ThreadingHTTPServer((host, port), make_handler(gateway))
    print(f"[sgw_proxy] listening on {host}:{port}", flush=True)
    print(f"[sgw_proxy] groups: {list(gateway.buckets)}", flush=True)
    print(f"[sgw_proxy] fingerprint log: {gateway.fp_dir}", flush=True)
    if gateway.disk_cache:
        print(f"[sgw_proxy] disk cache: {gateway.disk_cache.db_path} "
              f"(loaded {gateway._disk_load_count} entries in {gateway._disk_load_ms}ms)", flush=True)
    if gateway.state_manager:
        print(f"[sgw_proxy] circuit state: {gateway.state_manager.db_path}", flush=True)

    stopping = threading.Event()

    def shutdown(*_):
        if stopping.is_set():
            return
        stopping.set()
        print("\n[sgw_proxy] stopping...", flush=True)
        # BaseServer.shutdown 必须从 serve_forever 所在线程之外调用。
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        gateway.close()
        server.server_close()


if __name__ == "__main__":
    main()
