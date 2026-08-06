"""sina_finance 能力 — 新浪财报三表（资产负债/利润/现金流）。

把 asgk/base.py.sina_financial_report 的上游知识下沉到服务端：
  - URL: CompanyFinanceService.getFinanceReport2022
  - paperCode 构造（sh/sz 前缀 + 6位代码）
  - report_type 参数（fzb 资产负债 / lrb 利润 / llb 现金流）
  - 结构解析：result.data.report_list 是「按报告期(如 '20260331')为键」的 dict，
    每期含 data 数组（item_title/item_value/item_tongbi）

服务端把 report_list 解析为按报告期倒序的记录数组（每期一条 dict），客户端拿到
结构化数据。字段名（中文科目）原样保留——这是财报的本质（科目随报表类型不同），
客户端不需要字段映射知识。
"""
from __future__ import annotations

from typing import Any

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

_URL = ("https://quotes.sina.cn/cn/api/openapi.php/"
        "CompanyFinanceService.getFinanceReport2022")
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


@capability(
    name="sina_finance",
    domain="财报",
    sources=[SourceMeta(name="sina", group="sina")],
    default_source="sina",
    data_type="table",  # 按报告期倒序的记录数组
    cache_policy="quarterly",  # 财报按季度发布，发布即定稿
    supported_formats=["json", "csv", "md", "xlsx"],
)
def fetch_sina_finance(ctx: FetchContext, code: str, report_type: str = "lrb",
                       num: int = 8, **_unused) -> list[dict] | None:
    """新浪财报三表。

    Args:
        code: 6位股票代码
        report_type: fzb(资产负债) / lrb(利润) / llb(现金流)
        num: 取最近 N 期
    Returns:
        按报告期倒序的记录列表，每期一条 dict：
        {"报告期": "2026-03-31", "<科目>": "<值>", "<科目>_同比": <同比>, ...}
    """
    prefix = "sh" if code.startswith("6") else "sz"
    if not ctx.acquire():
        return None
    try:
        r = egress_request("get", ctx.source.egress_client, _URL,
                           params={"paperCode": f"{prefix}{code}", "source": report_type,
                                   "type": "0", "page": "1", "num": str(num)},
                           headers={"User-Agent": _UA}, timeout=15)
    except Exception:
        ctx.on_network_error()
        return None
    if r.status_code in (403, 429):
        ctx.on_failure(status=r.status_code, immediate=True)
        return None
    if r.status_code >= 500:
        ctx.on_failure(status=r.status_code)
        return None
    ctx.on_success()
    # report_list 按报告期(如 '20260331')为键
    report_list = r.json().get("result", {}).get("data", {}).get("report_list", {}) or {}
    rows: list[dict] = []
    for period in sorted(report_list.keys(), reverse=True)[:num]:
        obj = report_list[period]
        rec: dict[str, Any] = {"报告期": f"{period[:4]}-{period[4:6]}-{period[6:8]}"}
        for it in obj.get("data", []) or []:
            title = it.get("item_title", "")
            if not title or it.get("item_value") is None:
                continue
            rec[title] = it.get("item_value")
            tongbi = it.get("item_tongbi")
            if tongbi not in (None, ""):
                rec[title + "_同比"] = tongbi
        rows.append(rec)
    return rows
