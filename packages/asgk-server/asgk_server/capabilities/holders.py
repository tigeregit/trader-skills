"""holders 能力 — 东财十大股东/十大流通股东（emweb F10 端点）。

把 asgk/holders.py 的 top10_holders / top10_free_holders 上游知识下沉。
两函数共用 emweb PageSDGD/PageSDLTGD 端点 + code 大写 + date ISO 转换，
用 holder_type 参数区分（sdgd/sdltgd）。字段映射（HOLDER_RANK→rank 等）下沉。
"""
from __future__ import annotations

from typing import Any

from ..context import FetchContext
from ..registry import SourceMeta, capability
from .push2 import _egress_get

_EMWEB = "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch"


@capability(
    name="holders",
    domain="股东",
    sources=[SourceMeta(name="eastmoney", group="eastmoney")],
    default_source="eastmoney",
    data_type="table",
    cache_policy="quarterly",  # 季度定稿（按报告期），1天 TTL + 落盘
    supported_formats=["json", "csv", "md", "xlsx"],
)
def fetch_holders(ctx: FetchContext, symbol: str, date: str,
                  holder_type: str = "sdgd", **_unused) -> list[dict]:
    """十大股东/十大流通股东。holder_type ∈ {sdgd, sdltgd}，date=YYYYMMDD。

    返回结构化 [{rank, name, shares_type, hold_num, ratio, change, change_ratio,
    holder_type?}]。字段映射在服务端。
    """
    endpoint = "PageSDLTGD" if holder_type == "sdltgd" else "PageSDGD"
    resp_key = "sdltgd" if holder_type == "sdltgd" else "sdgd"
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"  # YYYYMMDD → YYYY-MM-DD
    data = _egress_get(ctx, f"{_EMWEB}/{endpoint}",
                       params={"code": symbol.upper(), "date": iso})
    if data is None:
        return None  # type: ignore[return-value]
    rows = data.get(resp_key, [])
    out = [{
        "rank": row.get("HOLDER_RANK"),
        "name": row.get("HOLDER_NAME") or "",
        "shares_type": row.get("SHARES_TYPE") or "",
        "hold_num": row.get("HOLD_NUM"),
        "ratio": row.get("HOLD_NUM_RATIO" if holder_type == "sdgd" else "FREE_HOLDNUM_RATIO"),
        "change": row.get("HOLD_NUM_CHANGE") or "",
        "change_ratio": row.get("CHANGE_RATIO"),
    } for row in rows]
    if holder_type == "sdltgd":
        for i, row in enumerate(rows):
            out[i]["holder_type"] = row.get("HOLDER_TYPE") or ""
    return out
