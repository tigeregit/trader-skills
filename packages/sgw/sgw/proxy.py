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

# 东财/同花顺等风控源走网关；其余(腾讯/百度/新浪/mootdx-TCP)直连不经网关
PROXIED_DOMAIN_SUFFIXES = (".eastmoney.com", ".10jqka.com.cn")

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

    def __init__(self, cooldown: float, failure_threshold: int):
        self.cooldown = cooldown
        self.failure_threshold = failure_threshold
        self._lock = threading.Lock()
        self._open_until = 0.0
        self._probe_in_flight = False
        self._failures = 0
        self.opens = 0

    def is_open(self) -> bool:
        with self._lock:
            return self._open_until > time.time()

    def before_request(self) -> bool:
        """返回是否可出网；冷却后只允许一个 canary。"""
        with self._lock:
            now = time.time()
            if self._open_until > now:
                return False
            if self._open_until:
                if self._probe_in_flight:
                    return False
                self._probe_in_flight = True
            return True

    def success(self) -> None:
        with self._lock:
            self._open_until = 0.0
            self._probe_in_flight = False
            self._failures = 0

    def failure(self, immediate: bool = False) -> None:
        with self._lock:
            self._probe_in_flight = False
            self._failures += 1
            if immediate or self._failures >= self.failure_threshold:
                self._open_until = time.time() + self.cooldown
                self.opens += 1

    def stats(self) -> dict:
        with self._lock:
            return {
                "open": self._open_until > time.time(),
                "open_until": self._open_until,
                "failures": self._failures,
                "opens": self.opens,
            }


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
                 cache_dir_override: str | None = None):
        self.cfg = config
        self._fp_dir_override = fp_dir_override
        self._cache_dir_override = cache_dir_override
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
        self.circuits = {
            name: CircuitBreaker(
                cooldown=float(circuit_cfg.get("cooldown_seconds", 300)),
                failure_threshold=int(circuit_cfg.get("failure_threshold", 3)),
            )
            for name in self.buckets
        }
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
               client_headers: dict | None = None) -> tuple[int, bytes, dict]:
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
                target_url, params, fwd_headers, policy, group, tier, ttl, cache_key
            )
        except Exception:
            result = (502, b'{"error":"gateway internal upstream failure"}', {"X-Cache-Tier": tier})
        self.singleflight.finish(cache_key, flight, result)
        return result

    def _fetch_upstream(self, target_url: str, params: dict, fwd_headers: dict,
                        policy: EndpointPolicy, group: str, tier: str, ttl: int,
                        cache_key: str) -> tuple[int, bytes, dict]:
        circuit = self.circuits[group]
        if circuit.is_open():
            return 503, b'{"error":"source circuit open; cache only"}', {"X-Cache-Tier": tier}

        # 构造一次规范 URL，确保真实请求与缓存身份对重复 query 参数的处理一致。
        request_url = _canonical_url(target_url, params)
        last_err = None
        for attempt in range(3):
            # 每次实际出网（包括重试）都必须重新经过全局限流。
            self.buckets[group].acquire()
            if not circuit.before_request():
                return 503, b'{"error":"source circuit open; cache only"}', {"X-Cache-Tier": tier}
            self.group_reqs[group] += 1
            try:
                r = requests.get(request_url, headers=fwd_headers, timeout=15)
                if r.status_code in (403, 429):
                    # 家庭 IP 不可更换：首次风控信号立即全来源熔断且不重试。
                    circuit.failure(immediate=True)
                    self.group_errs[group] += 1
                    return r.status_code, b'{"error":"source blocked; circuit opened"}', {"X-Cache-Tier": tier}
                if r.status_code in (500, 502, 503, 504):
                    circuit.failure()
                    last_err = r.status_code
                    if circuit.is_open() or attempt == 2:
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
                if attempt < 2 and not circuit.is_open():
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
            "cache": self.cache.stats(),
            "disk_cache": self.disk_cache.stats() if self.disk_cache else None,
            "disk_load_count": self._disk_load_count,
            "disk_load_ms": self._disk_load_ms,
            "intraday": self._is_intraday(),
        }


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
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    host = args.host or cfg["server"]["host"]
    port = args.port or cfg["server"]["port"]

    gateway = Gateway(cfg, fp_dir_override=args.fp_dir, cache_dir_override=args.cache_dir)
    server = ThreadingHTTPServer((host, port), make_handler(gateway))
    print(f"[sgw_proxy] listening on {host}:{port}", flush=True)
    print(f"[sgw_proxy] groups: {list(gateway.buckets)}", flush=True)
    print(f"[sgw_proxy] fingerprint log: {gateway.fp_dir}", flush=True)
    if gateway.disk_cache:
        print(f"[sgw_proxy] disk cache: {gateway.disk_cache.db_path} "
              f"(loaded {gateway._disk_load_count} entries in {gateway._disk_load_ms}ms)", flush=True)

    def shutdown(*_):
        print("\n[sgw_proxy] stopping...", flush=True)
        if gateway.disk_cache:
            gateway.disk_cache.close()
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
