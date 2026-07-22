#!/usr/bin/env python3
"""sgw_proxy — A股数据共享流量网关。

单进程 HTTP 代理，供单 IP 下 100~1000 个 agent 并发共享：
  - 按域名组令牌桶限流（东财组/同花顺组，全局串行，跨进程生效）
  - 五档缓存（P/L/S/R/N，TTL 由调用方用 X-Cache-Tier 头声明）
  - 429/5xx 退避、403 不重试
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
import threading
import time
import tomllib
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

import requests

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "sgw_config.toml"

# 东财/同花顺等风控源走网关；其余(腾讯/百度/新浪/mootdx-TCP)直连不经网关
PROXIED_DOMAIN_SUFFIXES = (".eastmoney.com", ".10jqka.com.cn")


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


# ── 网关主体 ──────────────────────────────────────────────────
class Gateway:
    def __init__(self, config: dict):
        self.cfg = config
        # 域名 → 组名
        self.domain_group: dict[str, str] = {}
        self.buckets: dict[str, TokenBucket] = {}
        for g in config.get("group", []):
            name = g["name"]
            jitter = tuple(g.get("jitter", [0, 0]))
            self.buckets[name] = TokenBucket(g["rps"], jitter)
            for d in g["domains"]:
                self.domain_group[d] = name
        self.cache = Cache()
        # 每组计数
        self.group_reqs: dict[str, int] = {n: 0 for n in self.buckets}
        self.group_errs: dict[str, int] = {n: 0 for n in self.buckets}
        # 指纹日志
        fp = config.get("fingerprint", {})
        self.fp_enabled = fp.get("enabled", False)
        self.fp_strip = set(fp.get("strip_fields", []))
        self.fp_path = HERE / fp.get("log_path", "sgw_fingerprint.jsonl") if self.fp_enabled else None
        self.fp_last_hash: dict[str, str] = {}  # key → 上次 resp_hash
        self.fp_lock = threading.Lock()

    # ── 域名归组 ──
    def group_of(self, host: str) -> Optional[str]:
        for suffix in PROXIED_DOMAIN_SUFFIXES:
            if host.endswith(suffix):
                return self.domain_group.get(host)
        return None

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
        if not self.fp_enabled or not self.fp_path:
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
        with open(self.fp_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ── 核心请求处理 ──
    def handle(self, target_url: str, params: dict, tier_header: Optional[str]) -> tuple[int, bytes, dict]:
        parsed = urlparse(target_url)
        host = parsed.netloc
        group = self.group_of(host)
        if group is None:
            # 非风控源不应走网关（直连即可），返回提示
            return 400, b'{"error":"domain not proxied, direct connect instead"}', {}

        # 档位：头声明优先，否则兜底
        tier = tier_header if tier_header in ("P", "L", "S", "R", "N") else self.fallback_tier(parsed.path)
        ttl = self.ttl_for_tier(tier)

        # 缓存 key = tier + 完整 URL（含 query）
        cache_key = f"{tier}|{target_url}"
        cached = self.cache.get(cache_key) if ttl > 0 else None
        if cached:
            body, headers = cached
            self._log_fingerprint(cache_key, tier, body, "cache_hit")
            return 200, body, {**headers, "X-Cache": "HIT", "X-Cache-Tier": tier}

        # 限流（全局串行）
        bucket = self.buckets[group]
        bucket.acquire()
        self.group_reqs[group] += 1

        # 请求外网（合并原始 params 与 target_url 自带 query）
        sep = "&" if "?" in target_url else "?"
        full_url = target_url if not params else f"{target_url}{sep}" + "&".join(f"{k}={v}" for k, v in params.items())
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        last_err = None
        for attempt in range(3):
            try:
                r = requests.get(target_url, params=params, headers=headers, timeout=15)
                if r.status_code == 403:
                    # 风控信号，不重试（§3.5）
                    self.group_errs[group] += 1
                    return 403, b'{"error":"403 blocked (rate limit signal)"}', {"X-Cache-Tier": tier}
                if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    last_err = r.status_code
                    time.sleep(0.6 * (2 ** attempt))  # 指数退避
                    continue
                # 成功
                resp_headers = {"Content-Type": r.headers.get("Content-Type", "application/json")}
                if ttl > 0:
                    self.cache.set(cache_key, r.content, resp_headers, ttl, tier)
                session = "intraday" if self._is_intraday() else "afterclose"
                self._log_fingerprint(cache_key, tier, r.content, session)
                return r.status_code, r.content, {**resp_headers, "X-Cache": "MISS", "X-Cache-Tier": tier}
            except requests.RequestException as e:
                last_err = str(e)
                if attempt < 2:
                    time.sleep(0.6 * (2 ** attempt))
                    continue
        self.group_errs[group] += 1
        return 502, json.dumps({"error": f"upstream failed: {last_err}"}).encode(), {"X-Cache-Tier": tier}

    def stats(self) -> dict:
        return {
            "group_reqs": self.group_reqs,
            "group_errs": self.group_errs,
            "bucket_waits": {n: b.wait_count for n, b in self.buckets.items()},
            "cache": self.cache.stats(),
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

            status, body, headers = gateway.handle(target_url, params, tier)
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
    ap = argparse.ArgumentParser(description="sgw_proxy — A股数据共享流量网关")
    ap.add_argument("-c", "--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    host = args.host or cfg["server"]["host"]
    port = args.port or cfg["server"]["port"]

    gateway = Gateway(cfg)
    server = ThreadingHTTPServer((host, port), make_handler(gateway))
    print(f"[sgw_proxy] listening on {host}:{port}", flush=True)
    print(f"[sgw_proxy] groups: {list(gateway.buckets)}", flush=True)
    print(f"[sgw_proxy] fingerprint log: {gateway.fp_path}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[sgw_proxy] stopped")
        server.shutdown()


if __name__ == "__main__":
    main()
