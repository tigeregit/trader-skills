"""quote 能力 — 腾讯实时行情（PE/PB/市值/换手/涨跌停）。

把 asgk/quote.py 的 tencent_quote 上游知识（qt.gtimg.cn URL、市场前缀 sh/sz/bj、
GBK 解码、53 字段映射）下沉到服务端。客户端只发 {codes: [...]}，服务端选源→
出网→解码→解析→返回结构化 {code: {price, pe_ttm, ...}}。

字段映射与 asgk/quote.py 完全一致，保证客户端零破坏（返回结构不变）。
"""
from __future__ import annotations

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

_QUOTE_URL = "https://qt.gtimg.cn/q"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


def _prefix(code: str) -> str:
    """6位代码 → 市场前缀（sh/sz/bj）。与 asgk/quote.py._prefix 一致。"""
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith("8"):
        return "bj"
    return "sz"


def _parse_tencent_payload(payload_gbk: str) -> dict[str, dict]:
    """解析腾讯 GBK 行情文本 → {code: {字段}}。

    与 asgk/quote.py.tencent_quote 的解析逻辑完全一致（53 字段映射）。
    一行一只票，形如 `v_sh600519="1~贵州茅台~...~"`；以 ; 分隔。
    """
    result: dict[str, dict] = {}
    for line in payload_gbk.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key[2:]
        result[code] = {
            "name": vals[1],
            "price": float(vals[3]) if vals[3] else 0,
            "last_close": float(vals[4]) if vals[4] else 0,
            "open": float(vals[5]) if vals[5] else 0,
            "change_amt": float(vals[31]) if vals[31] else 0,
            "change_pct": float(vals[32]) if vals[32] else 0,
            "high": float(vals[33]) if vals[33] else 0,
            "low": float(vals[34]) if vals[34] else 0,
            "amount_wan": float(vals[37]) if vals[37] else 0,
            "turnover_pct": float(vals[38]) if vals[38] else 0,
            "pe_ttm": float(vals[39]) if vals[39] else 0,
            "amplitude_pct": float(vals[43]) if vals[43] else 0,
            "mcap_yi": float(vals[44]) if vals[44] else 0,
            "float_mcap_yi": float(vals[45]) if vals[45] else 0,
            "pb": float(vals[46]) if vals[46] else 0,
            "limit_up": float(vals[47]) if vals[47] else 0,
            "limit_down": float(vals[48]) if vals[48] else 0,
            "vol_ratio": float(vals[49]) if vals[49] else 0,
            "pe_static": float(vals[52]) if vals[52] else 0,
        }
    return result


@capability(
    name="quote",
    domain="行情",
    sources=[SourceMeta(name="tencent", group="tencent")],
    default_source="tencent",
    data_type="kv",
    cache_policy="realtime",  # 实时型：no-cache，但走 singleflight 合并并发
    supported_formats=["json", "md"],
)
def fetch_quote(ctx: FetchContext, codes: list[str], source: str | None = None) -> dict[str, dict]:
    """腾讯实时行情。codes 为 6 位代码列表（也支持指数/ETF）。

    出网经 tencent 限流组（qt.gtimg.cn）。返回 GBK 文本，显式 decode("gbk")。
    fetch 内部用 ctx.acquire() 限流 + 熔断 canary，成功/失败反馈熔断器。
    """
    prefixed = [_prefix(c) + c for c in codes]
    if not ctx.acquire():
        # 熔断中：fetch 返回 None，server 转 503
        return None  # type: ignore[return-value]
    try:
        r = egress_request("get", ctx.source.egress_client, _QUOTE_URL,
                           params={"q": ",".join(prefixed)},
                           headers={"User-Agent": _UA})
    except Exception:
        ctx.on_network_error()
        return None  # type: ignore[return-value]
    if r.status_code in (403, 429):
        ctx.on_failure(status=r.status_code, immediate=True)
        return None  # type: ignore[return-value]
    if r.status_code >= 500:
        ctx.on_failure(status=r.status_code)
        return None  # type: ignore[return-value]
    ctx.on_success()
    return _parse_tencent_payload(r.content.decode("gbk"))
