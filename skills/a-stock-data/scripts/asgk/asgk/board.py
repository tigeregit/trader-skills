"""asgk.board — 板块层（概念/行业板块成份股）。

移植自 akshare stock_board_concept_cons_em / stock_board_industry_cons_em
（snapshot fcdbf25）。板块→成份股反向查询。
  - 端点：push2.eastmoney.com/api/qt/clist/get（主用无编号，[§7 决策7]）
  - 名称→板块代码：先调名称辅助请求（fs=m:90 t:3概念/t:2行业）
  - 分页：push2 用 pn/pz，按 data.total 判断（非 datacenter 的 pageNumber）
  - @source 档位：S（日级）
"""
from __future__ import annotations

import re

from asgk._contract import source
from asgk.em_proxy import _server_call, em_get

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"
_UT = "bd1d9ddb04089700cf9c27f6f7426281"
_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
# f12=代码 f14=名称 f2=最新价 f3=涨跌幅 f5=成交量 f6=成交额 f7=振幅 f8=换手率 f15/f16/f17=高/低/开
_FIELDS = "f12,f14,f2,f3,f5,f6,f7,f8,f15,f16,f17"


def _s(val) -> str:
    return val or ""


def _normalise_board_name(value: str) -> str:
    """兼容现网名称带“概念/板块/行业”等展示后缀。"""
    name = re.sub(r"\s+", "", value or "")
    for suffix in ("概念板块", "行业板块", "概念", "板块", "行业"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name


def _response_data(response) -> dict:
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if data is None:
        raise RuntimeError("东财板块接口返回 data=null")
    return data


def _resolve_board_code(symbol: str, kind: str) -> str:
    """板块名称 → 板块代码（BK 开头直接返回，否则查名称辅助表）。"""
    if re.match(r"^BK\d+", symbol):
        return symbol
    # 名称辅助：概念 fs=m:90 t:3，行业 fs=m:90 t:2
    t_code = "3" if kind == "concept" else "2"
    params = {"pn": "1", "pz": "200", "po": "1", "np": "1", "ut": _UT,
              "fltt": "2", "invt": "2", "fid": "f12", "fs": f"m:90 t:{t_code} f:!50",
              "fields": "f12,f14"}
    page = 1
    suffix_match = None
    while True:
        params["pn"] = str(page)
        r = em_get(_CLIST_URL, params=params, headers={"User-Agent": UA}, timeout=15, tier="S")
        d = _response_data(r)
        diff = d.get("diff") or []
        for item in diff:
            item_name = item.get("f14", "")
            if item_name == symbol:
                return item.get("f12")
            if (suffix_match is None
                    and _normalise_board_name(item_name) == _normalise_board_name(symbol)):
                suffix_match = item.get("f12")
        # total 判断是否还有页
        total = d.get("total", 0)
        if page * 200 >= total:
            if suffix_match:
                return suffix_match
            raise ValueError(f"未找到板块: {symbol}（kind={kind}）")
        page += 1


@source(tier="S", via="gateway", data_type="table")
def board_constituents(symbol: str, kind: str = "concept") -> list[dict]:
    """板块成份股（板块→成份股反向查询）。

    移植自 akshare stock_board_concept_cons_em / stock_board_industry_cons_em。
    push2 clist 端点（主用无编号 host）。

    Args:
        symbol: 板块名称（如"融资融券"/"小金属"）或板块代码（如 BK0655/BK1027）
        kind: "concept"(概念板块) / "industry"(行业板块)
    Returns:
        [{code, name, price(最新价), pct(涨跌幅,%), vol(成交量),
          amount(成交额,元), amplitude(振幅,%), turnover(换手率,%),
          high, low, open}, ...]

    取数路径（§3.4）：优先调 clist 能力（query_type=board_constituents），回退旧路径。
    """
    data = _server_call("clist", {"query_type": "board_constituents",
                                  "symbol": symbol, "kind": kind})
    if data is not None:
        return data
    return _board_constituents_legacy(symbol, kind)


def _board_constituents_legacy(symbol: str, kind: str = "concept") -> list[dict]:
    """回退路径：经 sgw 网关取 push2 clist（名称→代码 + 成份股分页）。"""
    board_code = _resolve_board_code(symbol, kind)
    params = {"po": "1", "np": "1", "ut": _UT, "fltt": "2", "invt": "2",
              "fid": "f12", "fs": f"b:{board_code} f:!50", "fields": _FIELDS}
    records: list[dict] = []
    page = 1
    while True:
        params["pn"] = str(page)
        params["pz"] = "100"
        r = em_get(_CLIST_URL, params=params, headers={"User-Agent": UA}, timeout=15, tier="S")
        d = _response_data(r)
        diff = d.get("diff") or []
        for item in diff:
            records.append({
                "code": _s(item.get("f12")),
                "name": _s(item.get("f14")),
                "price": item.get("f2"),
                "pct": item.get("f3"),
                "vol": item.get("f5"),
                "amount": item.get("f6"),
                "amplitude": item.get("f7"),
                "turnover": item.get("f8"),
                "high": item.get("f15"),
                "low": item.get("f16"),
                "open": item.get("f17"),
            })
        total = d.get("total", 0)
        if page * 100 >= total or not diff:
            break
        page += 1
    return records
