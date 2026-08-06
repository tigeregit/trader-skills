"""clist 能力 — 东财 clist 端点（行业排名 + 板块成份股）。

把 asgk/signal.py.industry_comparison 与 asgk/board.py.board_constituents 下沉。
两函数共用 push2 clist/get 端点 + diff 数组解析 + 分页，用 query_type 参数区分
（industry_rank / board_constituents）。

board_constituents 含两步流：名称→板块代码（clist 辅助查询）→ 成份股分页。
全部解析逻辑（f 字段映射、名称归一化匹配、total 分页）下沉服务端。
"""
from __future__ import annotations

import re
from typing import Any

from ..context import FetchContext
from ..registry import SourceMeta, capability
from .push2 import _egress_get

_CLIST = "https://push2.eastmoney.com/api/qt/clist/get"
_UT = "bd1d9ddb04089700cf9c27f6f7426281"
# 成份股字段：f12=代码 f14=名称 f2=最新价 f3=涨跌幅 f5=成交量 f6=成交额
#            f7=振幅 f8=换手率 f15/f16/f17=高/低/开
_CONS_FIELDS = "f12,f14,f2,f3,f5,f6,f7,f8,f15,f16,f17"


def _normalise_board_name(value: str) -> str:
    """兼容现网名称带"概念/板块/行业"等展示后缀。"""
    name = re.sub(r"\s+", "", value or "")
    for suffix in ("概念板块", "行业板块", "概念", "板块", "行业"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def _resolve_board_code(ctx: FetchContext, symbol: str, kind: str) -> str:
    """板块名称 → 板块代码（BK 开头直接返回，否则查名称辅助表分页匹配）。"""
    if re.match(r"^BK\d+", symbol):
        return symbol
    t_code = "3" if kind == "concept" else "2"
    params = {"pn": "1", "pz": "200", "po": "1", "np": "1", "ut": _UT,
              "fltt": "2", "invt": "2", "fid": "f12",
              "fs": f"m:90 t:{t_code} f:!50", "fields": "f12,f14"}
    page = 1
    suffix_match = None
    while True:
        params["pn"] = str(page)
        data = _egress_get(ctx, _CLIST, params=params)
        if data is None:
            raise ValueError(f"板块名称解析失败: {symbol}")
        d = data.get("data") or {}
        diff = d.get("diff") or []
        for item in diff:
            item_name = item.get("f14", "")
            if item_name == symbol:
                return item.get("f12")
            if (suffix_match is None
                    and _normalise_board_name(item_name) == _normalise_board_name(symbol)):
                suffix_match = item.get("f12")
        total = d.get("total", 0)
        if page * 200 >= total:
            if suffix_match:
                return suffix_match
            raise ValueError(f"未找到板块: {symbol}（kind={kind}）")
        page += 1


@capability(
    name="clist",
    domain="信号",
    sources=[SourceMeta(name="eastmoney", group="eastmoney")],
    default_source="eastmoney",
    data_type="kv",
    cache_policy="realtime",  # 行业排名/成份股盘中实时变
    supported_formats=["json", "md"],
)
def fetch_clist(ctx: FetchContext, query_type: str, top_n: int = 20,
                symbol: str = "", kind: str = "concept", **_unused) -> Any:
    """东财 clist 查询。query_type ∈ {industry_rank, board_constituents}。

    industry_rank:      全行业涨跌幅排名，返回 {top, bottom, total}
    board_constituents: 板块成份股（含名称→代码两步解析），返回成份股 list
    """
    if query_type == "industry_rank":
        data = _egress_get(ctx, _CLIST, params={
            "pn": "1", "pz": "100", "po": "1", "np": "1",
            "fltt": "2", "invt": "2", "fid": "f3", "fs": "m:90+t:2",
            "fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207",
        })
        if data is None:
            return None  # type: ignore[return-value]
        items = (data.get("data") or {}).get("diff", []) or []
        if not items:
            return {"top": [], "bottom": [], "total": 0}
        rows = [{
            "rank": i + 1, "name": it.get("f14", ""), "change_pct": it.get("f3", 0),
            "code": it.get("f12", ""), "up_count": it.get("f104", 0),
            "down_count": it.get("f105", 0), "leader": it.get("f140", ""),
            "leader_change": it.get("f136", 0),
        } for i, it in enumerate(items)]
        return {"top": rows[:top_n], "bottom": rows[-top_n:], "total": len(rows)}

    if query_type == "board_constituents":
        board_code = _resolve_board_code(ctx, symbol, kind)
        params = {"po": "1", "np": "1", "ut": _UT, "fltt": "2", "invt": "2",
                  "fid": "f12", "fs": f"b:{board_code} f:!50", "fields": _CONS_FIELDS,
                  "pz": "100"}
        records: list[dict] = []
        page = 1
        while True:
            params["pn"] = str(page)
            data = _egress_get(ctx, _CLIST, params=params)
            if data is None:
                return None  # type: ignore[return-value]
            d = data.get("data") or {}
            diff = d.get("diff") or []
            for item in diff:
                records.append({
                    "code": item.get("f12") or "", "name": item.get("f14") or "",
                    "price": item.get("f2"), "pct": item.get("f3"),
                    "vol": item.get("f5"), "amount": item.get("f6"),
                    "amplitude": item.get("f7"), "turnover": item.get("f8"),
                    "high": item.get("f15"), "low": item.get("f16"), "open": item.get("f17"),
                })
            total = d.get("total", 0)
            if page * 100 >= total or not diff:
                break
            page += 1
        return records

    return []
