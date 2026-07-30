"""asgk._datacenter — 东财数据中心共用查询（内部 helper）。

龙虎榜/解禁/融资融券/大宗交易/股东户数/分红/业绩/股东/事件 等端点共用同一
datacenter 接口，仅 reportName / filter / sort 不同。经网关（datacenter-web.eastmoney.com）。
"""
from __future__ import annotations

from asgk.em_proxy import em_get

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def datacenter(report_name: str, filter_str: str = "", page_size: int = 50,
               sort_columns: str = "", sort_types: str = "-1", tier: str = "S",
               *, all_pages: bool = False, max_pages: int | None = None,
               source: str = "WEB") -> list[dict]:
    """东财数据中心统一查询（经网关，已内置限流）。

    Args:
        report_name: 东财报表名，如 "RPT_DAILYBILLBOARD_DETAILSNEW"
        filter_str: 过滤条件，如 '(SECURITY_CODE="600519")'
        page_size: 每页条数
        sort_columns / sort_types: 排序字段/方向(-1降序)
        tier: 缓存档位（datacenter 多为日级定稿数据，默认 S）
        all_pages: True 时遍历所有页（全市场扫描场景，如业绩/回购/股东变化）。
            默认 False 只取第一页（向后兼容现有调用方）。
        max_pages: all_pages=True 时的最大页数上限（防失控，None=不限）。
        source: 东财端点 source 参数（datacenter-web 用 WEB；securities 端点用 HSF10）
    Returns:
        record 列表（原样返回 datacenter 的 data 数组），空则 []
    """
    if not all_pages:
        return _query_page(report_name, filter_str, page_size,
                           sort_columns, sort_types, tier, 1, source)

    # 全量分页：遍历 result.pages，聚合所有 data
    records: list[dict] = []
    page = 1
    while True:
        page_data, total_pages = _query_page_with_meta(
            report_name, filter_str, page_size,
            sort_columns, sort_types, tier, page, source,
        )
        records.extend(page_data)
        # 终止条件：无 pages 元信息 / 已到末页 / 触达 max_pages
        if not total_pages or page >= total_pages:
            break
        if max_pages is not None and page >= max_pages:
            break
        page += 1
    return records


def _query_page(report_name: str, filter_str: str, page_size: int,
                sort_columns: str, sort_types: str, tier: str,
                page_number: int, source: str) -> list[dict]:
    """查询单页，返回 data 数组（空则 []）。"""
    data, _ = _query_page_with_meta(
        report_name, filter_str, page_size,
        sort_columns, sort_types, tier, page_number, source,
    )
    return data


def _query_page_with_meta(report_name: str, filter_str: str, page_size: int,
                          sort_columns: str, sort_types: str, tier: str,
                          page_number: int, source: str) -> tuple[list[dict], int]:
    """查询单页，返回 (data 数组, 总页数)。

    总页数取自 result.pages；缺失或 result 为空时返回 ([], 0)。
    部分页失败（如中间页 5xx）由 em_get 重试机制处理；这里不做跨页重试。
    """
    params = {
        "reportName": report_name, "columns": "ALL",
        "filter": filter_str, "pageNumber": str(page_number), "pageSize": str(page_size),
        "sortColumns": sort_columns, "sortTypes": sort_types,
        "source": source, "client": "WEB",
    }
    r = em_get(DATACENTER_URL, params=params, timeout=15, tier=tier)
    d = r.json()
    result = d.get("result")
    if not result:
        return [], 0
    data = result.get("data") or []
    total_pages = result.get("pages", 0) or 0
    # pages 可能是字符串，统一转 int
    try:
        total_pages = int(total_pages)
    except (TypeError, ValueError):
        total_pages = 0
    return data, total_pages
