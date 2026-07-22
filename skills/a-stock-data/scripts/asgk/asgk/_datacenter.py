"""asgk._datacenter — 东财数据中心共用查询（内部 helper）。

龙虎榜/解禁/融资融券/大宗交易/股东户数/分红 等端点共用同一 datacenter 接口，
仅 reportName / filter / sort 不同。经网关（datacenter-web.eastmoney.com）。
"""
from __future__ import annotations

from asgk.em_proxy import em_get

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def datacenter(report_name: str, filter_str: str = "", page_size: int = 50,
               sort_columns: str = "", sort_types: str = "-1", tier: str = "S") -> list[dict]:
    """东财数据中心统一查询（经网关，已内置限流）。

    Args:
        report_name: 东财报表名，如 "RPT_DAILYBILLBOARD_DETAILSNEW"
        filter_str: 过滤条件，如 '(SECURITY_CODE="600519")'
        page_size: 每页条数
        sort_columns / sort_types: 排序字段/方向(-1降序)
        tier: 缓存档位（datacenter 多为日级定稿数据，默认 S）
    Returns:
        record 列表（原样返回 datacenter 的 data 数组），空则 []
    """
    params = {
        "reportName": report_name, "columns": "ALL",
        "filter": filter_str, "pageNumber": "1", "pageSize": str(page_size),
        "sortColumns": sort_columns, "sortTypes": sort_types,
        "source": "WEB", "client": "WEB",
    }
    r = em_get(DATACENTER_URL, params=params, timeout=15, tier=tier)
    d = r.json()
    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []
