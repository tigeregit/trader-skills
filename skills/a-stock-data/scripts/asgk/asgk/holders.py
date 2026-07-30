"""asgk.holders — 股东层（十大股东 / 十大流通股东 / 股东持股变化 / 股东协同）。

移植自 akshare stock_gdfx_top_10_em / stock_gdfx_free_top_10_em /
stock_gdfx_holding_change_em / stock_gdfx_holding_teamwork_em（snapshot fcdbf25）。
  - 十大股东/流通股东：emweb F10 端点（em_get 直调，单页 10 条）
  - 股东持股变化：datacenter（按报告期全市场，all_pages）
  - 股东协同：datacenter（按股东类型，all_pages）
  - @source 档位：L（季度定稿）
"""
from __future__ import annotations

from asgk._contract import source
from asgk._datacenter import datacenter as _datacenter
from asgk.em_proxy import em_get

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"

_EMWEB_BASE = "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch"


def _s(val) -> str:
    """None → 空串。"""
    return val or ""


def _date_to_iso(date: str) -> str:
    """YYYYMMDD → YYYY-MM-DD。"""
    return f"{date[:4]}-{date[4:6]}-{date[6:]}"


@source(tier="L", via="gateway")
def top10_holders(symbol: str, date: str) -> list[dict]:
    """十大股东明细（单股，按报告期）。

    移植自 akshare stock_gdfx_top_10_em。emweb F10 端点 PageSDGD。

    Args:
        symbol: 带市场前缀的代码，如 "sh600519"/"sz000001"（内部转大写）
        date: 报告期，YYYYMMDD（如 "20240930"）
    Returns:
        [{rank, name(股东名称), shares_type(股份类型), hold_num(持股数),
          ratio(占总股本比例,百分点 54.07=54.07%),
          change(增减), change_ratio(变动比率)}, ...] 共 10 条
    """
    r = em_get(f"{_EMWEB_BASE}/PageSDGD",
               params={"code": symbol.upper(), "date": _date_to_iso(date)},
               headers={"User-Agent": UA}, timeout=15, tier="L")
    data = r.json().get("sdgd", [])
    return [{
        "rank": row.get("HOLDER_RANK"),
        "name": _s(row.get("HOLDER_NAME")),
        "shares_type": _s(row.get("SHARES_TYPE")),
        "hold_num": row.get("HOLD_NUM"),
        "ratio": row.get("HOLD_NUM_RATIO"),  # 百分点
        "change": _s(row.get("HOLD_NUM_CHANGE")),
        "change_ratio": row.get("CHANGE_RATIO"),
    } for row in data]


@source(tier="L", via="gateway")
def top10_free_holders(symbol: str, date: str) -> list[dict]:
    """十大流通股东明细（单股，按报告期）。

    移植自 akshare stock_gdfx_free_top_10_em。emweb F10 端点 PageSDLTGD。

    Args:
        symbol: 带市场前缀的代码，如 "sh600519"
        date: 报告期，YYYYMMDD
    Returns:
        [{rank, name(股东名称), holder_type(股东类型), shares_type(股份类型),
          hold_num(持股数), ratio(占流通股比例,百分点),
          change(增减), change_ratio(变动比率)}, ...] 共 10 条
    """
    r = em_get(f"{_EMWEB_BASE}/PageSDLTGD",
               params={"code": symbol.upper(), "date": _date_to_iso(date)},
               headers={"User-Agent": UA}, timeout=15, tier="L")
    data = r.json().get("sdltgd", [])
    return [{
        "rank": row.get("HOLDER_RANK"),
        "name": _s(row.get("HOLDER_NAME")),
        "holder_type": _s(row.get("HOLDER_TYPE")),
        "shares_type": _s(row.get("SHARES_TYPE")),
        "hold_num": row.get("HOLD_NUM"),
        "ratio": row.get("FREE_HOLDNUM_RATIO"),  # 百分点
        "change": _s(row.get("HOLD_NUM_CHANGE")),
        "change_ratio": row.get("CHANGE_RATIO"),
    } for row in data]


@source(tier="L", via="gateway")
def holder_change(date: str) -> list[dict]:
    """股东持股变化统计（全市场，按报告期）。

    移植自 akshare stock_gdfx_holding_change_em。
    reportName=RPT_HOLDERS_BASIC_INFO。数据量极大（数万页），谨慎调用。

    Args:
        date: 报告期，YYYYMMDD（如 "20240930"），非股票代码
    Returns:
        [{holder_name(股东名称), holder_type(股东类型), holder_source(来源),
          holder_num(统计次数), holdup_num(增持家数), holddown_num(减持家数),
          holdadd_num(新进家数), holdunchanged_num(不变家数),
          holder_market_cap(持股市值,元), close_price(收盘价)}, ...]
    """
    filter_str = f" (END_DATE='{_date_to_iso(date)}')"
    data = _datacenter(
        "RPT_HOLDERS_BASIC_INFO", filter_str=filter_str,
        page_size=500, sort_columns="HOLDER_MARKET_CAP", sort_types="-1",
        tier="L", all_pages=True,
    )
    return [{
        "holder_name": _s(row.get("HOLDER_NAME")),
        "holder_type": _s(row.get("HOLDER_TYPE")),
        "holder_source": _s(row.get("HOLDER_SOURCE")),
        "holder_num": row.get("HOLDER_NUM", 0),
        "holdup_num": row.get("HOLDUP_NUM"),
        "holddown_num": row.get("HOLDDOWN_NUM"),
        "holdadd_num": row.get("HOLDADD_NUM"),
        "holdunchanged_num": row.get("HOLDUNCHANGED_NUM", 0),
        "holder_market_cap": row.get("HOLDER_MARKET_CAP"),  # 元
        "close_price": row.get("CLOSE_PRICE"),
    } for row in data]


@source(tier="L", via="gateway")
def holder_teamwork(holder_type: str = "全部") -> list[dict]:
    """股东协同（按股东类型）。

    移植自 akshare stock_gdfx_holding_teamwork_em。
    reportName=RPT_TENHOLDERS_COOPHOLDERS。

    Args:
        holder_type: 股东类型关键词，取值：全部/个人/基金/QFII/社保/券商/信托
    Returns:
        [{holder_name(股东名称), holder_type(类型),
          coop_holder_name(协同股东名称), coop_holder_type(协同股东类型),
          coop_num(协同次数)}, ...]
    """
    filter_str = "" if holder_type == "全部" else f'(HOLDER_TYPE="{holder_type}")'
    data = _datacenter(
        "RPT_TENHOLDERS_COOPHOLDERS", filter_str=filter_str,
        page_size=500, sort_columns="COOPERAT_NUM,HOLDER_NEW,COOPERAT_HOLDER_NEW",
        sort_types="-1,-1,-1", tier="L", all_pages=True,
    )
    return [{
        "holder_name": _s(row.get("HOLDER_NAME")),
        "holder_type": _s(row.get("HOLDER_TYPE")),
        "coop_holder_name": _s(row.get("COOPERAT_HOLDER_NAME")),
        "coop_holder_type": _s(row.get("COOPERAT_HOLDER_TYPE")),
        "coop_num": row.get("COOPERAT_NUM", 0),
    } for row in data]
