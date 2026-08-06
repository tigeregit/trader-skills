"""sina_option 能力 — 新浪 ETF 期权（合约清单/T型报价/希腊字母）。

把 asgk/option.py 三个函数的上游知识下沉到服务端：
  - sina_option_codes（S 档）：先取 contractMonth，再按月 N 次取合约清单
  - sina_option_tquote（R 档）：T 型报价，43 字段索引映射
  - sina_option_greeks（R 档）：希腊字母，⚠️ raw[1:4] 是 3 个空串必须跳过

三函数共用 hq.sinajs.cn/list 的 GBK 解析（去 var hq_str_XXX="..." 壳 → 逗号分隔），
但查询机制与 tier 不同：codes 是多步（先取月份再逐月），tquote/greeks 是单步。
用 option_type 参数区分（codes / tquote / greeks）。

字段索引映射（v[0]→bid_vol, v[1]→bid, ...）与跳空串逻辑（greeks 的 raw[1:4]）
全部下沉服务端。客户端拿结构化 dict，零字段索引知识。
"""
from __future__ import annotations

from typing import Any

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

_HQ_LIST = "https://hq.sinajs.cn/list"
_GETNAME = ("https://stock.finance.sina.com.cn/futures/api/openapi.php/"
            "StockOptionService.getStockName")
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"
_HDR = {"Referer": "https://stock.finance.sina.com.cn/", "User-Agent": _UA}
_CATE = {"510050": "50ETF", "510300": "300ETF",
         "588000": "科创50ETF", "510500": "500ETF"}


def _opt_f(x: Any) -> Any:
    """转 float，失败原样返回（如合约名是字符串）。"""
    try:
        return float(x)
    except Exception:
        return x


def _sina_opt_list(ctx: FetchContext, param: str, tier_acquire: bool = True) -> list:
    """新浪 hq.sinajs.cn 取值（GBK，逗号分隔，去 var hq_str_XXX="..." 壳）。

    tier_acquire=False 时跳过限流（codes 的逐月循环已由首请求 acquire，避免 N 次
    限流等待）；True 时正常 acquire（tquote/greeks 单请求）。
    """
    if tier_acquire and not ctx.acquire():
        return []
    try:
        r = egress_request("get", ctx.source.egress_client, _HQ_LIST,
                           params={"list": param}, headers=_HDR, timeout=10)
    except Exception:
        ctx.on_network_error()
        return []
    if r.status_code in (403, 429):
        ctx.on_failure(status=r.status_code, immediate=True)
        return []
    if r.status_code >= 500:
        ctx.on_failure(status=r.status_code)
        return []
    ctx.on_success()
    t = r.content.decode("gbk")
    return t.split('"')[1].split(",") if '"' in t else []


@capability(
    name="sina_option",
    domain="期权",
    sources=[SourceMeta(name="sina", group="sina")],
    default_source="sina",
    data_type="kv",  # 三个变体都返回 dict（codes 是 {月份:[代码]}）
    cache_policy="daily_settled",  # 合约清单 S 档日级；tquote/greeks R 但日级粒度够
    supported_formats=["json", "md"],
)
def fetch_sina_option(ctx: FetchContext, option_type: str, code: str = "",
                      underlying: str = "510050", call: bool = True,
                      **_unused) -> dict[str, Any] | None:
    """新浪 ETF 期权。option_type ∈ {codes, tquote, greeks}。

    codes:   {月份YYMM: [合约代码,...]}（需 underlying + call）；近月 key 在前
    tquote:  T型报价 dict（需 code）；字段索引 v[0..42] 映射
    greeks:  希腊字母 dict（需 code）；⚠️ 跳过 raw[1:4] 空串
    """
    if option_type == "codes":
        return _fetch_codes(ctx, underlying, call)
    if option_type == "tquote":
        return _fetch_tquote(ctx, code)
    if option_type == "greeks":
        return _fetch_greeks(ctx, code)
    return {}


def _fetch_codes(ctx: FetchContext, underlying: str, call: bool) -> dict:
    """合约清单：先取 contractMonth，再逐月取合约代码（N 次 hq 请求）。"""
    cate = _CATE.get(underlying, "50ETF")
    # 首请求：取 contractMonth（限流 + 熔断反馈）
    if not ctx.acquire():
        return {}
    try:
        r = egress_request("get", ctx.source.egress_client, _GETNAME,
                           params={"exchange": "null", "cate": cate},
                           headers=_HDR, timeout=10)
    except Exception:
        ctx.on_network_error()
        return {}
    if r.status_code in (403, 429):
        ctx.on_failure(status=r.status_code, immediate=True)
        return {}
    if r.status_code >= 500:
        ctx.on_failure(status=r.status_code)
        return {}
    ctx.on_success()
    try:
        months = r.json()["result"]["data"]["contractMonth"]
    except Exception:
        return {}
    months = [m.replace("-", "")[2:] for m in months[1:]]  # 丢首个，转 YYMM
    flag = "OP_UP_" if call else "OP_DOWN_"
    out: dict[str, list] = {}
    # 逐月取合约代码（共享首请求的限流配额，tier_acquire=False 不重复 acquire）
    for m in months:
        codes = [c.replace("CON_OP_", "") for c in _sina_opt_list(ctx, f"{flag}{underlying}{m}", tier_acquire=False)
                 if c.startswith("CON_OP_")]
        if codes:
            out[m] = codes
    return out


def _fetch_tquote(ctx: FetchContext, code: str) -> dict:
    """T 型报价：v[0..42] 字段索引映射。"""
    v = _sina_opt_list(ctx, f"CON_OP_{code}")
    if len(v) < 43:
        return {}
    return {"bid_vol": _opt_f(v[0]), "bid": _opt_f(v[1]), "last": _opt_f(v[2]),
        "ask": _opt_f(v[3]), "ask_vol": _opt_f(v[4]), "open_interest": _opt_f(v[5]),
        "pct": _opt_f(v[6]), "strike": _opt_f(v[7]), "prev_close": _opt_f(v[8]),
        "open": _opt_f(v[9]), "limit_up": _opt_f(v[10]), "limit_down": _opt_f(v[11]),
        "name": v[37], "amplitude": _opt_f(v[38]), "high": _opt_f(v[39]),
        "low": _opt_f(v[40]), "volume": _opt_f(v[41]), "amount": _opt_f(v[42])}


def _fetch_greeks(ctx: FetchContext, code: str) -> dict:
    """希腊字母：⚠️ raw[1:4] 是 3 个空串必须跳过（[raw[0]] + raw[4:]）。"""
    raw = _sina_opt_list(ctx, f"CON_SO_{code}")
    if len(raw) < 16:
        return {}
    v = [raw[0]] + raw[4:]  # 跳过 raw[1:4] 的 3 个空串
    return {"name": v[0], "volume": _opt_f(v[1]), "delta": _opt_f(v[2]),
        "gamma": _opt_f(v[3]), "theta": _opt_f(v[4]), "vega": _opt_f(v[5]),
        "iv": _opt_f(v[6]), "high": _opt_f(v[7]), "low": _opt_f(v[8]),
        "trade_code": v[9], "strike": _opt_f(v[10]), "last": _opt_f(v[11]), "theory": _opt_f(v[12])}
