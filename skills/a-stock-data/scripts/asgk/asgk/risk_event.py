"""asgk.risk_event — 事件层（高管增减持 / 股票回购 / 机构调研）。

移植自 akshare stock_hold_management_detail_em / stock_repurchase_em /
stock_jgdy_detail_em（snapshot fcdbf25）。全部全市场扫描，经 _datacenter
走网关（datacenter-web）。
  - 高管增减持/回购：无参，全市场明细
  - 机构调研：按开始日期过滤，全市场
  - 多页：all_pages=True 遍历所有页
  - @source 档位：S（日级）/ L（季度定稿）
"""
from __future__ import annotations

from asgk._contract import source
from asgk._datacenter import datacenter as _datacenter


def _s(val) -> str:
    """None → 空串（原始字段可能为 null）。"""
    return val or ""


@source(tier="S", via="gateway")
def mgmt_trade() -> list[dict]:
    """董监高持股变动明细（全市场，无参数）。

    移植自 akshare stock_hold_management_detail_em。
    reportName=RPT_EXECUTIVE_HOLD_DETAILS。

    Returns:
        [{code, name, change_date, person(变动人), position(职务),
          change_shares(变动股数,正增负减), avg_price(成交均价),
          change_amount(变动金额,元), change_reason(变动原因),
          change_ratio(变动比例), hold_after(变动后持股),
          hold_type(持股种类)}, ...]
    """
    data = _datacenter(
        "RPT_EXECUTIVE_HOLD_DETAILS", filter_str="",
        page_size=5000, sort_columns="CHANGE_DATE,SECURITY_CODE,PERSON_NAME",
        sort_types="-1,1,1", tier="S", all_pages=True,
    )
    return [{
        "code": row.get("SECURITY_CODE", ""),
        "name": _s(row.get("SECURITY_NAME")),
        "change_date": str(row.get("CHANGE_DATE", ""))[:10],
        "person": _s(row.get("PERSON_NAME")),
        "position": _s(row.get("POSITION_NAME")),
        "change_shares": row.get("CHANGE_SHARES", 0),
        "avg_price": row.get("AVERAGE_PRICE"),
        "change_amount": row.get("CHANGE_AMOUNT"),
        "change_reason": _s(row.get("CHANGE_REASON")),
        "change_ratio": row.get("CHANGE_RATIO"),
        "hold_after": row.get("CHANGE_AFTER_HOLDNUM"),
        "hold_type": _s(row.get("HOLD_TYPE")),
    } for row in data]


@source(tier="S", via="gateway")
def repurchase() -> list[dict]:
    """股票回购明细（全市场，无参数）。

    移植自 akshare stock_repurchase_em。
    reportName=RPTA_WEB_GETHGLIST_NEW。

    Returns:
        [{code, name, notice_date(公告日), start_date(回购起始), end_date(回购截止),
          progress(实施进度), plan_amt_lower/upper(计划金额区间,元),
          plan_num_lower/upper(计划数量区间,股), price_cap(计划价格上限),
          done_amt(已回购金额), done_num(已回购股数)}, ...]
    """
    data = _datacenter(
        "RPTA_WEB_GETHGLIST_NEW", filter_str="",
        page_size=500, sort_columns="UPDATEDATE", sort_types="-1",
        tier="S", all_pages=True,
    )
    # 进度代码 → 中文（akshare 原样返回代码，这里做易读映射）
    _PROGRESS = {"001": "实施中", "002": "完成", "003": "失败"}
    return [{
        "code": _s(row.get("DIM_SCODE")),
        "name": _s(row.get("SECURITYSHORTNAME")),
        "notice_date": str(row.get("UPDATEDATE", ""))[:10],
        "start_date": str(row.get("REPURSTARTDATE", ""))[:10],
        "end_date": str(row.get("REPURENDDATE", ""))[:10],
        "progress": _PROGRESS.get(_s(row.get("REPURPROGRESS")), _s(row.get("REPURPROGRESS"))),
        "plan_amt_lower": row.get("REPURAMOUNTLOWER"),
        "plan_amt_upper": row.get("REPURAMOUNTLIMIT"),
        "plan_num_lower": row.get("REPURNUMLOWER"),
        "plan_num_upper": row.get("REPURNUMCAP"),
        "price_cap": row.get("REPURPRICECAP"),
        "done_amt": row.get("REPURAMOUNT"),
        "done_num": row.get("REPURNUM"),
    } for row in data]


@source(tier="S", via="gateway")
def institute_research(start_date: str) -> list[dict]:
    """机构调研明细（全市场，按开始日期过滤）。

    移植自 akshare stock_jgdy_detail_em。
    reportName=RPT_ORG_SURVEY。用 columns=ALL（含全部调研字段）。

    Args:
        start_date: 调研开始日期，YYYYMMDD（如 "20241201"），返回此日期之后的调研
    Returns:
        [{code, name, notice_date(公告日), receive_date(调研日),
          receive_object(调研机构), receive_place(调研地点),
          receive_way(调研方式), investigators(调研人员),
          receptionist(接待人员), org_type(机构类型)}, ...]
    """
    iso = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
    filter_str = f'(IS_SOURCE="1")(RECEIVE_START_DATE>\'{iso}\')'
    data = _datacenter(
        "RPT_ORG_SURVEY", filter_str=filter_str,
        page_size=50, sort_columns="NOTICE_DATE,SECURITY_CODE",
        sort_types="-1,-1", tier="S", all_pages=True,
    )
    return [{
        "code": row.get("SECURITY_CODE", ""),
        "name": _s(row.get("SECURITY_NAME_ABBR")),
        "notice_date": str(row.get("NOTICE_DATE", ""))[:10],
        "receive_date": str(row.get("RECEIVE_START_DATE", ""))[:10],
        "receive_object": _s(row.get("RECEIVE_OBJECT")),
        "receive_place": _s(row.get("RECEIVE_PLACE")),
        "receive_way": _s(row.get("RECEIVE_WAY_EXPLAIN")),
        "investigators": _s(row.get("INVESTIGATORS")),
        "receptionist": _s(row.get("RECEPTIONIST")),
        "org_type": _s(row.get("ORG_TYPE")),
    } for row in data]
