"""asgk.pool_filter — 风险/筛选层（股权质押 / 商誉）。

移植自 akshare stock_gpzy_pledge_ratio_em / stock_sy_em（snapshot fcdbf25）。
全部按日期/报告期全市场扫描，经 _datacenter 走网关（datacenter-web）。
  - 股权质押：按交易日，全市场质押比例
  - 商誉：按报告期，全市场商誉明细（需固定 token）
  - 多页：all_pages=True 遍历所有页
  - @source 档位：S（日级）/ L（季度定稿）
"""
from __future__ import annotations

from asgk._contract import source
from asgk._datacenter import datacenter as _datacenter


def _s(val) -> str:
    """None → 空串。"""
    return val or ""


# 商誉接口的固定 token（akshare 源码硬编码，非动态签名）
_GOODWILL_TOKEN = "894050c76af8597a853f5b408b759f5d"


@source(tier="S", via="gateway")
def pledge_ratio(date: str) -> list[dict]:
    """股权质押比例（全市场，按交易日）。

    移植自 akshare stock_gpzy_pledge_ratio_em。
    reportName=RPT_CSDC_LIST。

    Args:
        date: 交易日，YYYYMMDD（如 "20240906"），非股票代码
    Returns:
        [{code, name, industry, trade_date,
          pledge_ratio(质押比例,小数 0.02=2%),
          pledge_deal_num(质押笔数),
          pledge_market_cap(质押市值,亿元),
          repurchase_balance(购回余额,万元),
          unrepurchase_balance(未购回余额,万元)}, ...]
    """
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    data = _datacenter(
        "RPT_CSDC_LIST", filter_str=f" (TRADE_DATE='{iso}')",
        page_size=500, sort_columns="SECURITY_CODE", sort_types="1",
        tier="S", all_pages=True,
    )
    return [{
        "code": row.get("SECURITY_CODE", ""),
        "name": _s(row.get("SECURITY_NAME_ABBR")),
        "industry": _s(row.get("INDUSTRY")),
        "trade_date": str(row.get("TRADE_DATE", ""))[:10],
        "pledge_ratio": row.get("PLEDGE_RATIO"),
        "pledge_deal_num": row.get("PLEDGE_DEAL_NUM", 0),
        "pledge_market_cap": row.get("PLEDGE_MARKET_CAP"),  # 亿元
        "repurchase_balance": row.get("REPURCHASE_BALANCE"),
        "unrepurchase_balance": row.get("REPURCHASE_UNLIMITED_BALANCE"),
    } for row in data]


@source(tier="L", via="gateway")
def goodwill(date: str) -> list[dict]:
    """商誉明细（全市场，按报告期）。

    移植自 akshare stock_sy_em。
    reportName=RPT_GOODWILL_STOCKDETAILS。需固定 token（非动态签名）。

    Args:
        date: 报告期，YYYYMMDD（如 "20231231"），非股票代码
    Returns:
        [{code, name, industry, report_date, notice_date,
          goodwill(商誉金额,元),
          goodwill_to_equity(商誉/净资产,小数 0.016=1.6%),
          net_profit(归母净利润,元),
          net_profit_yoy(净利同比,小数)}, ...]
    """
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    data = _datacenter(
        "RPT_GOODWILL_STOCKDETAILS", filter_str=f" (REPORT_DATE='{iso}')",
        page_size=5000, sort_columns="GOODWILL", sort_types="-1",
        tier="L", all_pages=True,
        extra_params={"token": _GOODWILL_TOKEN},
    )
    return [{
        "code": row.get("SECURITY_CODE", ""),
        "name": _s(row.get("SECURITY_NAME_ABBR")),
        "industry": _s(row.get("INDUSTRY_CFT")),
        "report_date": str(row.get("REPORT_DATE", ""))[:10],
        "notice_date": str(row.get("NOTICE_DATE", ""))[:10],
        "goodwill": row.get("GOODWILL"),  # 元
        "goodwill_to_equity": row.get("SUMSHEQUITY_RATIO"),
        "net_profit": row.get("PARENTNETPROFIT"),  # 元
        "net_profit_yoy": row.get("PNP_YOY_RATIO"),
    } for row in data]
