"""reports 能力 — 东财研报列表（reportapi，个股 + 行业）。

把 asgk/reports.py 的 eastmoney_reports / eastmoney_industry_reports 下沉。
两函数共用 reportapi/report/list 端点 + 分页逻辑，用 report_type 参数区分
（stock qType=0 / industry qType=1）。字段映射原样返回 data 数组（研报字段多，
  调用方按需取，不强映射）。
"""
from __future__ import annotations

from typing import Any

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

_REPORT_API = "https://reportapi.eastmoney.com/report/list"
_REFERER = {"Referer": "https://data.eastmoney.com/"}


@capability(
    name="reports",
    domain="研报",
    sources=[SourceMeta(name="eastmoney", group="eastmoney")],
    default_source="eastmoney",
    data_type="table",
    cache_policy="definitive",  # 研报发布即定稿，30天 TTL + 落盘
    supported_formats=["json", "csv", "md", "xlsx"],
)
def fetch_reports(ctx: FetchContext, report_type: str = "stock",
                  code: str = "*", industry_code: str = "*",
                  max_pages: int = 5, begin: str = "2024-01-01",
                  **_unused) -> list[dict]:
    """东财研报列表。report_type ∈ {stock, industry}。

    stock:    个股研报（qType=0，按 code）
    industry: 行业研报（qType=1，按 industry_code）
    返回研报 record 列表（原样 data 数组，含 title/publishDate/orgSName 等字段）。
    """
    all_records: list[dict] = []
    for page in range(1, max_pages + 1):
        params: dict[str, str] = {
            "industryCode": industry_code, "pageSize": "100", "industry": "*",
            "rating": "*", "ratingChange": "*",
            "beginTime": begin, "endTime": "2030-01-01",
            "pageNo": str(page), "fields": "",
            "qType": "1" if report_type == "industry" else "0",
        }
        if report_type == "stock":
            params.update({"orgCode": "", "code": code, "rcode": "",
                           "p": str(page), "pageNum": str(page),
                           "pageNumber": str(page)})
        if not ctx.acquire():
            return None  # type: ignore[return-value]
        try:
            r = egress_request("get", ctx.source.egress_client, _REPORT_API,
                               params=params, headers=_REFERER, timeout=30)
        except Exception:
            ctx.on_network_error()
            return None  # type: ignore[return-value]
        if r.status_code in (403, 429):
            ctx.on_failure(status=r.status_code, immediate=True)
            return None  # type: ignore[return-value]
        if r.status_code >= 500:
            ctx.on_failure(status=r.status_code)
            return None  # type: ignore[return-value]
        ctx.on_success()
        d = r.json()
        rows = d.get("data") or []
        if not rows:
            break
        all_records.extend(rows)
        if page >= (d.get("TotalPage", 1) or 1):
            break
    return all_records
