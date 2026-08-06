"""asgk_server.server — HTTP JSON RPC 入口 + 流量内核中间件。

契约（§3.1）：
  POST /v1/<capability>   {"semantic_params": ..., "source": "可选"}
      → 结构化数据（dict/list），与现有业务函数返回值一致
  GET  /v1/sources         ?capability=quote
      → ["tencent","sina",...] 或全部能力映射

请求处理流水线（§4 复用 sgw 流量内核）：
  选源(显式或自动) → 缓存命中? → singleflight 合并 → 状态闩 → 熔断 →
  限流 → 出网 → 熔断反馈 → 写缓存 → 返回

T1 阶段：服务端可启动、GET /v1/sources 返回空、mock 能力可注册可调用。
真实能力（quote/kline/...）在 T2~T10 填充。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import signal
import threading
import time
import tomllib
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

import requests

from . import registry
from .egress import egress_request
from .traffic import (
    Cache,
    CircuitBreaker,
    CircuitStateManager,
    DiskCache,
    SingleFlight,
    TokenBucket,
)

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "config.toml"

_DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


class CapabilityServer:
    """能力代理服务端主体：持有流量内核 + 能力注册表，处理语义请求。

    选源策略（§3.1）：
      - 客户端传 source → 强制走该源（熔断则报错，不降级）
      - 不传 source → default_source，熔断则按 sources 顺序降级到下一健康源
    """

    def __init__(self, config: dict, cache_dir_override: str | None = None,
                 state_dir_override: str | None = None,
                 fp_dir_override: str | None = None):
        self.cfg = config
        self._closed = False
        self._cache_dir_override = cache_dir_override
        self._state_dir_override = state_dir_override
        self._fp_dir_override = fp_dir_override

        # 限流组：group 名 → TokenBucket；域名/源 → group 名（域名归组由 config 驱动）
        self.domain_group: dict[str, str] = {}
        self.buckets: dict[str, TokenBucket] = {}
        for g in config.get("group", []):
            name = g["name"]
            jitter = tuple(g.get("jitter", [0, 0]))
            self.buckets[name] = TokenBucket(g["rps"], jitter)
            for d in g.get("domains", []):
                self.domain_group[d] = name
        if not self.buckets:
            raise ValueError("no rate-limit groups configured; fail closed")

        # 熔断：每组一个 CircuitBreaker，由 on_change 回写状态库
        circuit_cfg = config.get("circuit", {})
        retry_cfg = config.get("retry", {})
        self.max_attempts = max(1, int(retry_cfg.get("max_attempts", 3)))
        state_cfg = config.get("state", {})
        self.state_manager: Optional[CircuitStateManager] = None
        initial_circuit_states: dict[str, dict] = {}
        if state_cfg.get("enabled", False):
            state_dir = Path(self._state_dir_override or state_cfg.get("dir", "state"))
            if not state_dir.is_absolute():
                state_dir = HERE / state_dir
            self.state_manager = CircuitStateManager(
                state_dir,
                set(self.buckets),
                backoff=state_cfg.get("backoff_seconds"),
            )
            initial_circuit_states = self.state_manager.initial_states
        self.circuits: dict[str, CircuitBreaker] = {
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

        # singleflight + 内存缓存 + 磁盘缓存（T1 沿用 sgw；T1.5 改造 cache）
        self.singleflight = SingleFlight()
        self.cache = Cache()
        self.group_reqs: dict[str, int] = {n: 0 for n in self.buckets}
        self.group_errs: dict[str, int] = {n: 0 for n in self.buckets}
        self.disk_cache: Optional[DiskCache] = None
        self._disk_load_count = 0
        self._disk_load_ms = 0
        persist = config.get("cache", {}).get("persist", {})
        if persist.get("enabled", False):
            cache_dir = Path(self._cache_dir_override or persist.get("dir", "cache"))
            if not cache_dir.is_absolute():
                cache_dir = HERE / cache_dir
            tiers = set(persist.get("tiers", ["P", "L"]))
            self.disk_cache = DiskCache(cache_dir / "asgk_cache.db", tiers)
            t0 = time.time()
            for key, (body, headers, expire, tier) in self.disk_cache.load_all().items():
                self.cache._store[key] = (body, headers, expire, tier)
            self._disk_load_count = len(self.cache._store)
            self._disk_load_ms = round((time.time() - t0) * 1000, 1)

        # 指纹日志（沿用 sgw §3.4.7）
        fp = config.get("fingerprint", {})
        self.fp_enabled = fp.get("enabled", False)
        self.fp_strip = set(fp.get("strip_fields", []))
        self.fp_dir = Path(self._fp_dir_override or fp.get("log_dir", "logs"))
        if not self.fp_dir.is_absolute():
            self.fp_dir = HERE / self.fp_dir
        self.fp_last_hash: dict[str, str] = {}
        self.fp_lock = threading.Lock()

    # ── 熔断状态持久化（沿用 sgw）──
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

    # ── 档位 → TTL（T1 沿用 sgw 五档；T1.5 改为 cache_policy 分档）──
    def ttl_for_tier(self, tier: str) -> int:
        c = self.cfg["cache"]
        if tier == "P":
            return c["P_ttl"]
        if tier == "L":
            return c["L_ttl"]
        if tier == "S":
            return c["S_ttl_afterclose"] if not self._is_intraday() else c["S_ttl_session"]
        return 0  # R / N / 未知 → no-cache

    def _is_intraday(self) -> bool:
        """简化交易时段判断：工作日 09:00-18:00。MVP，P4 校准。"""
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        s = self.cfg["cache"]["session"]
        t = now.strftime("%H:%M")
        return s["intraday_start"] <= t < s["intraday_end"]

    # ── 选源（§3.1 核心契约）──
    def _resolve_source(self, meta: registry.CapabilityMeta,
                        source: str | None) -> registry.SourceMeta:
        """解析出健康源。显式指定不降级；不指定则 default + 熔断降级。"""
        if source is not None:
            sm = meta.source(source)  # KeyError → 400 unknown source
            if not sm.healthy:
                raise SourceUnhealthy(f"source {source!r} is open (circuit)")
            return sm
        # 自动选源：default_source 优先，熔断则按 sources 顺序降级
        for sm in meta.sources:
            if sm.healthy:
                return sm
        raise SourceUnhealthy(f"no healthy source for capability {meta.name!r}")

    # ── 熔断状态回写 source.healthy ──
    def _sync_source_health(self) -> None:
        """把熔断器开关状态同步到 SourceMeta.healthy（自动选源用）。"""
        for meta in registry.list_capabilities().values():
            for sm in meta.sources:
                circuit = self.circuits.get(sm.group)
                sm.healthy = circuit is None or not circuit.is_open()

    # ── 处理一次能力请求（核心流水线）──
    def handle_capability(self, capability_name: str, params: dict
                          ) -> tuple[int, dict]:
        """处理 POST /v1/<capability>。

        返回 (http_status, response_dict)。response_dict 含 data 或 error。
        params 可含 source（可选）+ 语义参数。
        """
        try:
            meta, fetch = registry.get_capability(capability_name)
        except KeyError:
            return 404, {"error": f"unknown capability: {capability_name}"}

        # source 是控制参数，不传给 fetch 的语义参数
        source = params.pop("source", None)

        # 熔断状态同步到 SourceMeta（决定自动选源能否降级）
        self._sync_source_health()
        try:
            sm = self._resolve_source(meta, source)
        except KeyError:
            return 400, {"error": f"unknown source: {source!r} for {capability_name}",
                         "available": meta.source_names()}
        except SourceUnhealthy as e:
            return 503, {"error": str(e)}

        group = sm.group
        tier = self._tier_for_cache_policy(meta.cache_policy)
        ttl = self.ttl_for_tier(tier)

        # cache key：capability|source|semantic_key（T1.5 接入 _semantic_key）
        cache_key = self._cache_key(capability_name, sm.name, params)

        # 命中缓存（TTL>0 才查）
        if ttl > 0:
            cached = self._cache_get(cache_key)
            if cached is not None:
                return 200, {"data": cached, "cache": "HIT-MEM", "source": sm.name}
        # singleflight 合并并发 miss（即使 TTL=0 也合并，避免实时端点冷 miss 风暴）
        leader, flight = self.singleflight.join(cache_key)
        if not leader:
            if not flight.event.wait(timeout=30):
                return 504, {"error": "coalesced request timed out"}
            assert flight.result is not None
            status, payload = flight.result
            if status == 200:
                payload = {**payload, "cache": "COALESCED"}
            return status, payload

        # leader：执行 fetch，带流量内核保护（状态闩 → 熔断 → 限流 → 出网）
        try:
            result = self._execute_fetch(meta, fetch, sm, group, params, tier, ttl, cache_key)
        except SourceBlocked as e:
            result = (503, {"error": str(e)})
        except Exception:
            result = (502, {"error": "capability fetch failed"})
        self.singleflight.finish(cache_key, flight, result)

        status, payload = result
        if status == 200:
            payload = {**payload, "source": sm.name}
        return status, payload

    def _execute_fetch(self, meta: registry.CapabilityMeta, fetch,
                       sm: registry.SourceMeta, group: str, params: dict,
                       tier: str, ttl: int, cache_key: str) -> tuple[int, dict]:
        """leader 路径：状态闩 → 熔断 → 限流 → 调 fetch → 反馈 → 写缓存。

        fetch 函数体内自行调用 egress_request 出网（服务端持有全部上游知识）。
        本方法提供流量保护上下文：在调用前后驱动熔断/限流/状态闩。
        fetch 通过 ctx（FetchContext）拿到限流 acquire 与熔断反馈入口。
        """
        # 状态闩：异常期禁止受控源出网
        if self.state_manager is not None:
            allowed, recovered = self.state_manager.before_egress()
            if recovered:
                self._restore_circuit_states(recovered)
            if not allowed:
                raise SourceBlocked("state store safety latch open; cache only")

        circuit = self.circuits[group]
        if circuit.is_open():
            raise SourceBlocked(f"source {sm.name!r} circuit open; cache only")

        ctx = FetchContext(
            group=group,
            bucket=self.buckets[group],
            circuit=circuit,
            source_meta=sm,
            max_attempts=self.max_attempts,
        )
        try:
            data = fetch(ctx=ctx, **params)
        except requests.RequestException:
            ctx.on_network_error()
            raise
        except SourceBlocked:
            raise
        # fetch 内部已通过 ctx.on_success/on_failure 反馈熔断
        if ctx.last_status in (403, 429):
            return 403, {"error": "source blocked; circuit opened"}
        if ctx.failed:
            return 502, {"error": "upstream failed", "reason": ctx.last_status}

        # 写缓存（TTL>0 才写）
        if ttl > 0 and data is not None:
            self._cache_set(cache_key, data, ttl, tier)
        return 200, {"data": data, "cache": "MISS"}

    # ── cache 辅助（T1 沿用 sgw 字节语义；T1.5 改为结构化 JSON）──
    def _tier_for_cache_policy(self, policy: str) -> str:
        """cache_policy → 五档 tier 映射（T1 临时方案；T1.5 直查六类分档表）。"""
        return {
            "definitive": "P", "quarterly": "L", "daily_settled": "S",
            "daily_volatile": "S", "realtime": "R", "streaming": "N",
        }.get(policy, "R")

    def _cache_key(self, capability: str, source: str, params: dict) -> str:
        """capability|source|semantic_key（T1.5 接入 _semantic_key 排序去重）。"""
        raw = json.dumps(params, sort_keys=True, ensure_ascii=False)
        param_hash = hashlib.md5(raw.encode()).hexdigest()[:16]
        return f"{capability}|{source}|{param_hash}"

    def _cache_get(self, key: str):
        cached = self.cache.get(key)
        if cached is not None:
            body, _headers = cached
            try:
                return json.loads(body)
            except (ValueError, json.JSONDecodeError):
                return None
        if self.disk_cache is not None:
            disk = self.disk_cache.get(key)
            if disk is not None:
                body, _headers = disk
                try:
                    data = json.loads(body)
                except (ValueError, json.JSONDecodeError):
                    return None
                # 回填内存
                ttl = self._ttl_for_key(key)
                if ttl > 0:
                    self.cache.set(key, body, {"Content-Type": "application/json"}, ttl, "P")
                return data
        return None

    def _ttl_for_key(self, key: str) -> int:
        """从 cache key 反推 tier（磁盘回填用，粗略）。"""
        # key 形如 capability|source|hash；tier 由 cache_policy 决定，回填用 P 保守值
        return self.cfg["cache"]["P_ttl"]

    def _cache_set(self, key: str, data, ttl: int, tier: str) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json"}
        self.cache.set(key, body, headers, ttl, tier)
        if self.disk_cache is not None:
            self.disk_cache.set(key, body, headers, ttl, tier)

    # ── 统计 ──
    def stats(self) -> dict:
        return {
            "capabilities": list(registry.list_capabilities()),
            "group_reqs": self.group_reqs,
            "group_errs": self.group_errs,
            "bucket_waits": {n: b.wait_count for n, b in self.buckets.items()},
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


class FetchContext:
    """能力函数的流量上下文：fetch 内部用它 acquire 限流、反馈熔断。

    fetch 约定：
      - 出网前调 ctx.acquire()（限流 + 熔断 canary 判定）
      - 成功调 ctx.on_success()，失败调 ctx.on_failure(status, immediate)
      - 或对 requests 异常用 ctx.on_network_error()
    """

    def __init__(self, group: str, bucket: TokenBucket, circuit: CircuitBreaker,
                 source_meta: registry.SourceMeta, max_attempts: int):
        self.group = group
        self.bucket = bucket
        self.circuit = circuit
        self.source = source_meta
        self.max_attempts = max_attempts
        self.failed = False
        self.last_status: int | str | None = None

    def acquire(self) -> bool:
        """限流 + 熔断 canary 判定。返回 False 表示熔断中不可出网。"""
        self.bucket.acquire()
        return self.circuit.before_request()

    def on_success(self) -> None:
        self.circuit.success()
        self.last_status = 200

    def on_failure(self, status: int | None = None, *, immediate: bool = False) -> None:
        self.circuit.failure(immediate=immediate, status=status)
        self.last_status = status
        if immediate or status in (500, 502, 503, 504):
            self.failed = True

    def on_network_error(self) -> None:
        self.circuit.failure()
        self.failed = True
        self.last_status = "network"


class SourceBlocked(Exception):
    """熔断/状态闩打开，受控源不可出网。"""


class SourceUnhealthy(Exception):
    """指定源熔断或无健康源可用。"""


# ── HTTP handler ──────────────────────────────────────────────
def make_handler(server: CapabilityServer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # 静默默认日志

        def do_GET(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)

            if parsed.path == "/__stats":
                self._json(200, server.stats())
                return

            if parsed.path == "/v1/sources":
                cap = qs.get("capability", [None])[0]
                if cap is None:
                    # 全部能力 → {capability: [sources]}
                    out = {name: m.source_names()
                           for name, m in registry.list_capabilities().items()}
                else:
                    try:
                        meta, _fn = registry.get_capability(cap)
                    except KeyError:
                        self._json(404, {"error": f"unknown capability: {cap}"})
                        return
                    out = meta.source_names()
                self._json(200, out)
                return

            self._json(404, {"error": f"unknown path: {parsed.path}"})

        def do_POST(self):
            parsed = urlparse(self.path)
            # POST /v1/<capability>
            prefix = "/v1/"
            if not parsed.path.startswith(prefix):
                self._json(404, {"error": f"unknown path: {parsed.path}"})
                return
            capability = parsed.path[len(prefix):]
            if not capability or "/" in capability:
                self._json(404, {"error": f"unknown capability: {capability}"})
                return

            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                params = json.loads(raw) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"error": "POST body must be valid JSON"})
                return
            if not isinstance(params, dict):
                self._json(400, {"error": "POST body must be a JSON object"})
                return

            status, payload = server.handle_capability(capability, params)
            self._json(status, payload)

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    ap = argparse.ArgumentParser(description="asgk-server - 能力代理服务端")
    ap.add_argument("-c", "--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--cache-dir", default=None,
                    help="磁盘缓存目录（P/L 档持久化，生产建议 /var/lib/asgk-server）")
    ap.add_argument("--state-dir", default=None,
                    help="熔断状态与安全标记目录（生产建议 /var/lib/asgk-server/state）")
    ap.add_argument("--fp-dir", default=None,
                    help="指纹日志目录（生产环境必须指定）")
    ap.add_argument("--max-attempts", type=int, default=None,
                    help="覆盖上游最大尝试次数；真实 canary 必须设为 1")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    if args.max_attempts is not None:
        cfg.setdefault("retry", {})["max_attempts"] = max(1, args.max_attempts)
    host = args.host or cfg["server"]["host"]
    port = args.port or cfg["server"]["port"]

    server = CapabilityServer(
        cfg,
        cache_dir_override=args.cache_dir,
        state_dir_override=args.state_dir,
        fp_dir_override=args.fp_dir,
    )
    httpd = ThreadingHTTPServer((host, port), make_handler(server))
    print(f"[asgk-server] listening on {host}:{port}", flush=True)
    print(f"[asgk-server] groups: {list(server.buckets)}", flush=True)
    print(f"[asgk-server] capabilities: {list(registry.list_capabilities()) or '(none yet)'}",
          flush=True)
    if server.disk_cache:
        print(f"[asgk-server] disk cache: {server.disk_cache.db_path} "
              f"(loaded {server._disk_load_count} entries in {server._disk_load_ms}ms)",
              flush=True)
    if server.state_manager:
        print(f"[asgk-server] circuit state: {server.state_manager.db_path}", flush=True)

    def shutdown(*_):
        print("\n[asgk-server] stopping...", flush=True)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        httpd.server_close()


if __name__ == "__main__":
    main()
