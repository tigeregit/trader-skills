"""push2 单端点能力 — 东财个股信息 / 板块归属（push2.eastmoney.com）。

把 asgk/base.py.eastmoney_stock_info 与 asgk/signal.py.eastmoney_concept_blocks
的上游知识（push2 URL、secid 市场前缀、f 字段表、slist 参数）下沉到服务端。

两个函数共用 secid 构造逻辑（6 开头→sh market_code=1，否则 0），但端点与字段
不同，故分两个具名能力：stock_info（stock/get）与 concept_blocks（slist/get）。

客户端发 {code}，服务端构造 secid + 出网 + 返回解析后结构化数据。
字段映射下沉到此（f57→code 等），客户端零上游知识。
"""
from __future__ import annotations

from typing import Any

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

_PUSH2 = "https://push2.eastmoney.com/api/qt"
_REFERER = "https://quote.eastmoney.com/"


def _secid(code: str) -> str:
    """6位代码 → 东财 secid（市场码.代码）。6/9 开头→sh(1)，否则 sz(0)。"""
    market_code = 1 if code.startswith(("6", "9")) else 0
    return f"{market_code}.{code}"


def _egress_get(ctx: FetchContext, url: str, params: dict,
                headers: dict | None = None) -> Any:
    """通用 push2 GET：限流→出网→熔断反馈→返回 r.json()。失败返回 None。"""
    h = {"Referer": _REFERER}
    if headers:
        h.update(headers)
    if not ctx.acquire():
        return None
    try:
        r = egress_request("get", ctx.source.egress_client, url, params=params,
                           headers=h, timeout=15)
    except Exception:
        ctx.on_network_error()
        return None
    if r.status_code in (403, 429):
        ctx.on_failure(status=r.status_code, immediate=True)
        return None
    if r.status_code >= 500:
        ctx.on_failure(status=r.status_code)
        return None
    ctx.on_success()
    try:
        return r.json()
    except ValueError:
        return None


@capability(
    name="stock_info",
    domain="基础数据",
    sources=[SourceMeta(name="eastmoney", group="eastmoney")],
    default_source="eastmoney",
    data_type="kv",
    cache_policy="daily_settled",  # 基本面信息日级（盘中 S 盘后定稿）
    supported_formats=["json", "md"],
)
def fetch_stock_info(ctx: FetchContext, code: str, **_unused) -> dict[str, Any]:
    """东财个股基本面信息。

    字段映射下沉：f57→code, f58→name, f127→industry, f84/f85→股本,
    f116/f117→市值, f189→上市日, f43→现价。客户端拿到的就是结构化 dict。
    """
    data = _egress_get(ctx, f"{_PUSH2}/stock/get", params={
        "fltt": "2", "invt": "2",
        "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43",
        "secid": _secid(code),
    })
    if data is None:
        return None  # type: ignore[return-value]
    d = data.get("data") or {}
    return {
        "code": d.get("f57", ""), "name": d.get("f58", ""),
        "industry": d.get("f127", ""),
        "total_shares": d.get("f84", 0), "float_shares": d.get("f85", 0),
        "mcap": d.get("f116", 0), "float_mcap": d.get("f117", 0),
        "list_date": str(d.get("f189", "")), "price": d.get("f43", 0),
    }


@capability(
    name="concept_blocks",
    domain="信号",
    sources=[SourceMeta(name="eastmoney", group="eastmoney")],
    default_source="eastmoney",
    data_type="kv",
    cache_policy="daily_settled",
    supported_formats=["json", "md"],
)
def fetch_concept_blocks(ctx: FetchContext, code: str, **_unused) -> dict[str, Any]:
    """个股所属板块/概念归属（东财 slist，一次请求拿全）。

    字段映射下沉：diff 数组 → boards[{name(f14), code(f12), change_pct(f3),
    lead_stock(f128)}] + concept_tags。客户端拿到结构化 dict。
    """
    data = _egress_get(ctx, f"{_PUSH2}/slist/get", params={
        "fltt": "2", "invt": "2", "secid": _secid(code),
        "spt": "3", "pi": "0", "pz": "200", "po": "1",
        "fields": "f12,f14,f3,f128",
    })
    if data is None:
        return None  # type: ignore[return-value]
    diff = (data.get("data") or {}).get("diff") or {}
    items = diff.values() if isinstance(diff, dict) else diff
    boards = [{
        "name": it.get("f14", ""),
        "code": it.get("f12", ""),
        "change_pct": it.get("f3", ""),
        "lead_stock": it.get("f128", ""),
    } for it in items]
    return {"total": len(boards), "boards": boards,
            "concept_tags": [b["name"] for b in boards]}
