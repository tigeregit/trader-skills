"""mootdx 能力 — 通达信 TCP(7709) 客户端池（K线/五档/逐笔/财务/F10）。

这是新架构相对透明代理的最大收益：5 个原本直连的 TCP 函数首次走代理，纳入
限流+熔断保护，所有 agent 共享一个客户端池（而非各自建连）。

把 asgk/client.py.tdx_client（mootdx 0.11.x BESTIP.HQ 空串 bug 的探测兜底链）
+ asgk/quote.py.mootdx_bars/quotes/transaction + asgk/base.py.mootdx_finance/f10
的全部上游知识下沉到服务端：
  - TCP 服务器探测列表（_TDX_SERVERS）+ 顺序兜底（探测→bestip→factory）
  - 客户端池（线程安全 lazy-init，长连接复用）
  - mootdx DataFrame → list[dict] 转换
  - mootdx_bars 的日线百度降级链（部分节点返回空日 K）

用 mootdx_type 参数区分五变体（bars/quotes/transaction/finance/f10）。

客户端发 {mootdx_type, code/symbols/frequency/offset/date/name}，服务端出网
（TCP）+ 返回结构化数据。客户端零 TCP 知识。

注：mootdx TCP 不封 IP，故 mootdx 组的 rps=2.0（config.toml）是保守自律值，
避免单点连接耗尽，非风控要求。
"""
from __future__ import annotations

import socket
import threading
from typing import Any

from ..context import FetchContext
from ..registry import SourceMeta, capability

# 2026-06 实测可用的通达信服务器（从 asgk/client.py 搬入，按延迟排序）
_TDX_SERVERS = [
    ("119.97.185.59", 7709), ("124.70.133.119", 7709), ("116.205.183.150", 7709),
    ("123.60.73.44", 7709), ("116.205.163.254", 7709), ("121.36.225.169", 7709),
    ("123.60.70.228", 7709), ("124.71.9.153", 7709), ("110.41.147.114", 7709),
    ("124.71.187.122", 7709),
]

# 客户端池（线程安全 lazy-init；服务端单例，所有 agent 共享）
_client_lock = threading.Lock()
_client: Any = None
_client_unavailable: Exception | None = None  # 首次初始化失败的异常缓存


def _probe(ip: str, port: int, timeout: float = 2.0) -> bool:
    """TCP 握手探测，判断服务器是否可达。"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def _build_client() -> Any:
    """创建 mootdx 客户端，规避 0.11.x BESTIP.HQ 空串 bug。

    顺序兜底（与 asgk/client.py.tdx_client 一致）：
      1) 顺序探测 _TDX_SERVERS，用第一个 TCP 可达的显式 server；
      2) 全部不可达 → 回退 mootdx 自带 bestip 测速选优；
      3) 再不行 → 回退裸 factory；
      4) 仍失败 → 抛 RuntimeError。
    """
    from mootdx.quotes import Quotes
    for ip, port in _TDX_SERVERS:
        if _probe(ip, port):
            return Quotes.factory(market="std", server=(ip, port))
    try:
        return Quotes.factory(market="std", bestip=True)
    except Exception:
        pass
    return Quotes.factory(market="std")


def _get_client() -> Any:
    """获取池化的 mootdx 客户端（线程安全 lazy-init）。

    首次调用建连（探测兜底链）；后续复用。首次失败缓存异常，避免每次请求都重试
    探测（探测 10 个 TCP + bestip 测速耗时数秒）。
    """
    global _client, _client_unavailable
    if _client is not None:
        return _client
    if _client_unavailable is not None:
        raise _client_unavailable
    with _client_lock:
        if _client is not None:
            return _client
        if _client_unavailable is not None:
            raise _client_unavailable
        try:
            _client = _build_client()
            return _client
        except Exception as e:
            _client_unavailable = e
            raise


def _df_to_records(df) -> list[dict]:
    """mootdx DataFrame → list[dict]（None/空 → []）。"""
    if df is None or len(df) == 0:
        return []
    return df.to_dict("records")


@capability(
    name="mootdx",
    domain="行情",
    sources=[SourceMeta(name="mootdx", group="mootdx")],
    default_source="mootdx",
    data_type="table",  # 五变体返回 list[dict]（finance 是 dict 但走 table 兼容）
    cache_policy="daily_settled",  # K线/财务日级；五档/逐笔 R 但日级缓存粒度可接受
    supported_formats=["json", "csv", "md", "xlsx"],
)
def fetch_mootdx(ctx: FetchContext, mootdx_type: str, code: str = "",
                 symbols: list | None = None, frequency: int = 9, offset: int = 100,
                 date: str | None = None, name: str = "公司概况", **_unused) -> Any:
    """通达信 TCP 查询。mootdx_type ∈ {bars, quotes, transaction, finance, f10}。

    bars:        K线（需 code, frequency, offset）；日线空响应时降级百度
    quotes:      五档盘口（需 symbols: [code,...]）
    transaction: 逐笔成交（需 code, date 可选）
    finance:     财务快照（需 code），返回 dict
    f10:         F10 文本（需 code, name）
    """
    if not ctx.acquire():
        return None
    try:
        client = _get_client()
    except Exception:
        ctx.on_network_error()
        return None

    try:
        if mootdx_type == "bars":
            return _fetch_bars(ctx, client, code, frequency, offset)
        if mootdx_type == "quotes":
            recs = _df_to_records(client.quotes(symbol=symbols or [code]))
            ctx.on_success()
            return recs
        if mootdx_type == "transaction":
            recs = _df_to_records(
                client.transaction(symbol=code, date=date) if date
                else client.transaction(symbol=code))
            ctx.on_success()
            return recs
        if mootdx_type == "finance":
            df = client.finance(symbol=code)
            ctx.on_success()
            if df is None or len(df) == 0:
                return {}
            return df.to_dict("records")[0]
        if mootdx_type == "f10":
            text = client.F10(symbol=code, name=name) or ""
            ctx.on_success()
            return text
        ctx.on_success()
        return []
    except OSError:
        # TCP 网络层错误（连接断/超时）→ 网络错误反馈
        ctx.on_network_error()
        # 清池重置：连接可能已坏，下次重建
        _reset_client()
        return None
    except Exception:
        ctx.on_failure(status=500)
        return None


def _fetch_bars(ctx: FetchContext, client: Any, code: str,
                frequency: int, offset: int) -> list[dict]:
    """K线：mootdx 取数，日线空响应降级百度（与 asgk/quote.py 一致）。

    mootdx 0.11.7 部分节点返回空日 K；日线(4/9)可安全降级百度，分钟/周/月不降级。
    """
    records = _df_to_records(
        client.bars(symbol=code, frequency=frequency, offset=offset))
    if records or frequency not in (4, 9):
        ctx.on_success()
        return records

    # 日线降级百度（与 asgk/quote.py.mootdx_bars 降级逻辑一致）
    # 此处复用同进程的 baidu_kline capability 取数（已下沉服务端）。
    ctx.on_success()  # mootdx 自身调用成功（只是空）
    baidu = _baidu_kline_fallback(code)
    if baidu is None:
        return []
    keys = baidu.get("keys") or []
    out = []
    for line in (baidu.get("rows") or [])[-offset:]:
        row = dict(zip(keys, line.split(",")))
        try:
            out.append({
                "open": float(row["open"]), "close": float(row["close"]),
                "high": float(row["high"]), "low": float(row["low"]),
                "vol": float(row["volume"]), "amount": float(row["amount"]),
                "datetime": row["time"],
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _baidu_kline_fallback(code: str) -> dict | None:
    """调本服务端的 baidu_kline capability 做日 K 降级（避开客户端再往返）。"""
    from ..registry import get_capability
    try:
        cap_meta, fn = get_capability("baidu_kline")
    except KeyError:
        return None
    # 构造一个轻量 ctx（不复用 mootdx 的 ctx，避免限流配额串用）
    sub = FetchContext(
        group=cap_meta.sources[0].group,
        bucket=ctx.bucket,  # 限流桶共享（同请求粒度）
        circuit=ctx.circuit,  # 熔断器共享
        source_meta=cap_meta.sources[0],
        max_attempts=ctx.max_attempts,
    )
    try:
        return fn(ctx=sub, code=code)
    except Exception:
        return None


def _reset_client() -> None:
    """清池重置（TCP 连接坏时调用，下次 _get_client 重建）。"""
    global _client, _client_unavailable
    with _client_lock:
        _client = None
        _client_unavailable = None
