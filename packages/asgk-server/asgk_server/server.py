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
from .cache import JsonDiskCache, SemanticCache, semantic_key
from .cache_policy import resolve_ttl, should_persist
from .context import FetchContext, SourceBlocked, SourceUnhealthy
from .egress import egress_request
from .traffic import (
    CircuitBreaker,
    CircuitStateManager,
    SingleFlight,
    TokenBucket,
)
# 导入 capabilities 包触发各 @capability 注册（真实数据能力）。
# 放在 registry/context 之后，确保装饰器与 FetchContext 可用。
from . import capabilities as _capabilities  # noqa: F401

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "config.toml"

_DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _host_of(url: str) -> str:
    """从 URL 提取小写 host（emquery 按域名归限流组用）。"""
    from urllib.parse import urlparse
    return (urlparse(url).hostname or "").lower()


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

        # singleflight + 语义缓存（结构化内存 + JSON 文件落盘，§3.6）
        # 取代 sgw 的字节 Cache + SQLite DiskCache。cache key = capability|source|语义键。
        self.singleflight = SingleFlight()
        self.group_reqs: dict[str, int] = {n: 0 for n in self.buckets}
        self.group_errs: dict[str, int] = {n: 0 for n in self.buckets}
        self._disk_load_count = 0
        self._disk_load_ms = 0
        disk_cache: Optional[JsonDiskCache] = None
        persist = config.get("cache", {}).get("persist", {})
        if persist.get("enabled", False):
            cache_dir = Path(self._cache_dir_override or persist.get("dir", "cache"))
            if not cache_dir.is_absolute():
                cache_dir = HERE / cache_dir
            disk_cache = JsonDiskCache(cache_dir)
        self.cache = SemanticCache(disk_cache)
        if disk_cache is not None:
            t0 = time.time()
            for key, (value, expire) in disk_cache.load_all().items():
                self.cache.preload(key, value, expire)
            self._disk_load_count = self.cache.memory.stats()["size"]
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

    # ── cache_policy → TTL（§3.6c 六类数据型分档，取代五档 tier）──
    def _ttl_for_policy(self, policy: str) -> int:
        """把能力的 cache_policy 解析为具体 TTL（秒）。

        daily_settled 随交易时段变（盘中0/盘后12h）；其余固定。未知 policy 保守 0。
        """
        return resolve_ttl(policy, is_intraday_fn=self._is_intraday)

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
        # emquery 是 URL 级通用能力：限流组按 URL 的域名动态归组
        # （push2→eastmoney, data.hexin→10jqka 等），而非 SourceMeta 固定的 eastmoney。
        # 未知域名（无对应限流组）拒绝出网（fail-closed）。
        if capability_name == "emquery" and isinstance(params.get("url"), str):
            resolved = self.domain_group.get(_host_of(params["url"]))
            if resolved is None:
                return 400, {"error": f"emquery: 域名无对应限流组: {params['url']!r}"}
            group = resolved
        policy = meta.cache_policy
        ttl = self._ttl_for_policy(policy)
        persist = should_persist(policy)

        # cache key：capability|source|语义键（§3.6b/f，per-source 独立不跨源共享）
        cache_key = semantic_key(capability_name, sm.name, params)

        # 命中缓存（TTL>0 才查；realtime/streaming 的 TTL=0 不查）
        if ttl > 0:
            cached = self.cache.get(cache_key)
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
            result = self._execute_fetch(meta, fetch, sm, group, params, ttl, persist, cache_key)
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
                       ttl: int, persist: bool, cache_key: str) -> tuple[int, dict]:
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
        # 计数（统计用）：每次实际尝试出网递增该组请求数
        self.group_reqs[group] = self.group_reqs.get(group, 0) + 1
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

        # 写缓存（结构化数据；TTL>0 才写，persist 决定是否落盘，§3.6）
        if ttl > 0 and data is not None:
            self.cache.set(cache_key, data, ttl, persist)
        return 200, {"data": data, "cache": "MISS"}

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
            "disk_load_count": self._disk_load_count,
            "disk_load_ms": self._disk_load_ms,
            "intraday": self._is_intraday(),
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.cache.disk:
            self.cache.disk.close()
        if self.state_manager:
            self.state_manager.close()


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
    if server.cache.disk:
        print(f"[asgk-server] disk cache: {server.cache.disk.cache_dir} "
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
