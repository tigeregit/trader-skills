"""datacenter 能力 — 东财数据中心统一查询（15 个业务函数共用）。

把 asgk/_datacenter.py 的查询机制（datacenter-web.eastmoney.com URL、参数构造、
分页遍历）下沉到服务端。业务函数（融资融券/大宗/股东户数/分红/业绩/事件/质押/
商誉/股东/龙虎榜/解禁）的 reportName/filter/sort 各不同，但都调这一个能力——
服务端返回 result.data 原始记录，字段映射（纯计算）留在客户端（§6.3）。

与 asgk/_datacenter.py.datacenter 的契约完全一致：
  输入：report_name / filter_str / sort_columns / sort_types / page_size /
        all_pages / max_pages / source / extra_params
  输出：record 列表（原样返回 datacenter 的 data 数组），空则 []

分页逻辑（all_pages=True 遍历 result.pages 聚合）原样搬入。
"""
from __future__ import annotations

from typing import Any

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _query_page(ctx: FetchContext, report_name: str, filter_str: str,
                page_size: int, sort_columns: str, sort_types: str,
                page_number: int, source: str,
                extra_params: dict | None) -> tuple[list[dict], int]:
    """查询单页，返回 (data 数组, 总页数)。"""
    params: dict[str, str] = {
        "reportName": report_name, "columns": "ALL",
        "filter": filter_str, "pageNumber": str(page_number),
        "pageSize": str(page_size),
        "sortColumns": sort_columns, "sortTypes": sort_types,
        "source": source, "client": "WEB",
    }
    if extra_params:
        params.update({k: str(v) for k, v in extra_params.items()})
    if not ctx.acquire():
        return [], 0  # 熔断中
    try:
        r = egress_request("get", ctx.source.egress_client, _DATACENTER_URL,
                           params=params, timeout=15)
    except Exception:
        ctx.on_network_error()
        return [], 0
    if r.status_code in (403, 429):
        ctx.on_failure(status=r.status_code, immediate=True)
        return [], 0
    if r.status_code >= 500:
        ctx.on_failure(status=r.status_code)
        return [], 0
    ctx.on_success()
    d = r.json()
    result = d.get("result")
    if not result:
        return [], 0
    data = result.get("data") or []
    total_pages = result.get("pages", 0) or 0
    try:
        total_pages = int(total_pages)
    except (TypeError, ValueError):
        total_pages = 0
    return data, total_pages


@capability(
    name="datacenter",
    domain="数据中心",
    sources=[SourceMeta(name="eastmoney", group="eastmoney")],
    default_source="eastmoney",
    data_type="table",
    # datacenter 数据多档（S/L/P），由调用方 tier 映射；这里取日级定稿的保守默认。
    # 业务函数按自身 @source tier 决定档位，但能力层只认 cache_policy——故取
    # daily_settled（盘中0/盘后12h）作为多数 datacenter 数据的合理默认。
    # 单页查询是幂等的，realtime 不合适（会禁缓存导致重复全量分页）。
    cache_policy="daily_settled",
    supported_formats=["json", "md"],
)
def fetch_datacenter(ctx: FetchContext, report_name: str,
                     filter_str: str = "", page_size: int = 50,
                     sort_columns: str = "", sort_types: str = "-1",
                     all_pages: bool = False, max_pages: int | None = None,
                     dc_source: str = "WEB",
                     extra_params: dict | None = None,
                     **_unused) -> list[dict]:
    """东财数据中心统一查询。

    与 asgk/_datacenter.py.datacenter 签名一致（去掉了 tier，由能力层 cache_policy 驱动；
    端点 source 参数改名 dc_source，避开能力的 source 选源控制参数）。
    返回 record 列表（原样 data 数组）。分页逻辑：all_pages=True 遍历 result.pages。
    """
    if not all_pages:
        data, _ = _query_page(ctx, report_name, filter_str, page_size,
                              sort_columns, sort_types, 1, dc_source, extra_params)
        return data

    records: list[dict] = []
    page = 1
    while True:
        page_data, total_pages = _query_page(
            ctx, report_name, filter_str, page_size,
            sort_columns, sort_types, page, dc_source, extra_params,
        )
        records.extend(page_data)
        if not total_pages or page >= total_pages:
            break
        if max_pages is not None and page >= max_pages:
            break
        page += 1
    return records
