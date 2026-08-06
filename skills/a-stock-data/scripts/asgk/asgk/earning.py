"""asgk.earning — 业绩层（业绩预告 / 业绩快报）。

移植自 akshare stock_yjyg_em / stock_yjkb_em（snapshot fcdbf25）。
按 reportDate 全市场扫描（非单股查询），经 _datacenter 走网关（datacenter-web）。
  - 报告期接口：date="20240930" → filter (REPORT_DATE='2024-09-30')，全市场返回
  - 多页：all_pages=True 遍历所有页
  - @source 档位：L（季度定稿）
"""
from __future__ import annotations

from asgk._contract import source
from asgk._datacenter import datacenter as _datacenter


def _date_to_iso(date: str) -> str:
    """YYYYMMDD → YYYY-MM-DD（东财 datacenter filter 的报告期格式）。"""
    return f"{date[:4]}-{date[4:6]}-{date[6:]}"


def _s(val) -> str:
    """None → 空串（原始字段可能为 null）。"""
    return val or ""


@source(tier="L", via="gateway", data_type="table")
def earning_forecast(date: str) -> list[dict]:
    """业绩预告（全市场，按报告期扫描）。

    移植自 akshare stock_yjyg_em。reportName=RPT_PUBLIC_OP_NEWPREDICT。

    Args:
        date: 报告期，YYYYMMDD（如 "20240930"），非股票代码
    Returns:
        [{code, name, notice_date, report_date, predict_finance(预测指标),
          predict_lower, predict_upper(预测金额区间,元),
          add_amp_lower, add_amp_upper(变动幅度区间,%),
          predict_type(预告类型), predict_content(业绩变动说明),
          preyear_same(上年同期值)}, ...]
    """
    filter_str = f" (REPORT_DATE='{_date_to_iso(date)}')"
    data = _datacenter(
        "RPT_PUBLIC_OP_NEWPREDICT", filter_str=filter_str,
        page_size=500, sort_columns="NOTICE_DATE,SECURITY_CODE", sort_types="-1,-1",
        tier="L", all_pages=True,
    )
    return [{
        "code": row.get("SECURITY_CODE", ""),
        "name": _s(row.get("SECURITY_NAME_ABBR")),
        "notice_date": str(row.get("NOTICE_DATE", ""))[:10],
        "report_date": str(row.get("REPORT_DATE", ""))[:10],
        "predict_finance": _s(row.get("PREDICT_FINANCE")),
        "predict_lower": row.get("PREDICT_AMT_LOWER"),
        "predict_upper": row.get("PREDICT_AMT_UPPER"),
        "add_amp_lower": row.get("ADD_AMP_LOWER"),
        "add_amp_upper": row.get("ADD_AMP_UPPER"),
        "predict_type": _s(row.get("PREDICT_TYPE")),
        "predict_content": _s(row.get("PREDICT_CONTENT")),
        "preyear_same": row.get("PREYEAR_SAME_PERIOD"),
    } for row in data]


@source(tier="L", via="gateway", data_type="table")
def earning_express(date: str) -> list[dict]:
    """业绩快报（全市场，按报告期扫描）。

    移植自 akshare stock_yjkb_em。reportName=RPT_FCI_PERFORMANCEE。

    Args:
        date: 报告期，YYYYMMDD（如 "20240930"），非股票代码
    Returns:
        [{code, name, notice_date, report_date,
          eps(基本每股收益), bvps(每股净资产),
          operate_income(营业收入,元), operate_income_sq(去年同期营收),
          net_profit(净利润,元), net_profit_sq(去年同期净利),
          income_yoy(营收同比,%), profit_yoy(净利同比,%),
          roe(加权净资产收益率,%)}, ...]
    """
    filter_str = f" (REPORT_DATE='{_date_to_iso(date)}')"
    data = _datacenter(
        "RPT_FCI_PERFORMANCEE", filter_str=filter_str,
        page_size=500, sort_columns="UPDATE_DATE,SECURITY_CODE", sort_types="-1,-1",
        tier="L", all_pages=True,
    )
    return [{
        "code": row.get("SECURITY_CODE", ""),
        "name": _s(row.get("SECURITY_NAME_ABBR")),
        "notice_date": str(row.get("NOTICE_DATE", ""))[:10],
        "report_date": str(row.get("REPORT_DATE", ""))[:10],
        "eps": row.get("BASIC_EPS"),
        "bvps": row.get("PARENT_BVPS"),
        "operate_income": row.get("TOTAL_OPERATE_INCOME"),
        "operate_income_sq": row.get("TOTAL_OPERATE_INCOME_SQ"),
        "net_profit": row.get("PARENT_NETPROFIT"),
        "net_profit_sq": row.get("PARENT_NETPROFIT_SQ"),
        "income_yoy": row.get("YSTZ"),
        "profit_yoy": row.get("JLRTBZCL"),
        "roe": row.get("WEIGHTAVG_ROE"),
    } for row in data]
