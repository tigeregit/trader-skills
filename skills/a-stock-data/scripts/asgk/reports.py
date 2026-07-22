"""asgk.reports — 研报层（东财，经网关）。

移植自 ref/a-stock-data SKILL.md §2.1。按 asgk-contract.md 契约：
  - @source(tier="P", via="gateway")：研报发布即定稿，P档长缓存(30天)
  - 经 em_get 走网关（全局限流+缓存）
  - 返回结构化 list[dict]
"""
from __future__ import annotations

from asgk._contract import source
from asgk.em_proxy import em_get

REPORT_API = "https://reportapi.eastmoney.com/report/list"
_REFERER = {"Referer": "https://data.eastmoney.com/"}


@source(tier="P", via="gateway", cli="report")
def eastmoney_reports(code: str, max_pages: int = 5) -> list[dict]:
    """拉取指定股票的研报列表（评级 + 三年EPS预测）。

    Args:
        code: 6位股票代码，如 "688017"
        max_pages: 最多翻页数，每页100条
    Returns:
        研报 record 列表，字段见 ref §2.1（title/publishDate/orgSName/
        infoCode/predictThisYearEps/emRatingName/indvInduName 等）。
    """
    all_records: list[dict] = []
    for page in range(1, max_pages + 1):
        params = {
            "industryCode": "*", "pageSize": "100", "industry": "*",
            "rating": "*", "ratingChange": "*",
            "beginTime": "2000-01-01", "endTime": "2030-01-01",
            "pageNo": str(page), "fields": "", "qType": "0",
            "orgCode": "", "code": code, "rcode": "",
            "p": str(page), "pageNum": str(page), "pageNumber": str(page),
        }
        r = em_get(REPORT_API, params=params, headers=_REFERER, timeout=30, tier="P")
        d = r.json()
        rows = d.get("data") or []
        if not rows:
            break
        all_records.extend(rows)
        if page >= (d.get("TotalPage", 1) or 1):
            break
    return all_records


@source(tier="P", via="gateway")
def eastmoney_industry_reports(industry_code: str = "*", max_pages: int = 5,
                               begin: str = "2024-01-01") -> list[dict]:
    """拉取行业研报列表（qType=1）。

    Args:
        industry_code: "*"=全行业；传东财行业码（如 "1238"=IT服务Ⅱ）= 单行业。
            行业码无公开码表端点，先用 "*" 拉一批从结果的 industryCode 字段反查。
        max_pages: 最多翻页数
        begin: 起始日期 "YYYY-MM-DD"
    Returns:
        行业研报 record 列表（含 industryName/industryCode/emRatingName/infoCode 等）。
    """
    all_records: list[dict] = []
    for page in range(1, max_pages + 1):
        params = {
            "industryCode": industry_code, "pageSize": "100", "industry": "*",
            "rating": "*", "ratingChange": "*",
            "beginTime": begin, "endTime": "2030-01-01",
            "pageNo": str(page), "fields": "", "qType": "1",
        }
        r = em_get(REPORT_API, params=params, headers=_REFERER, timeout=30, tier="P")
        d = r.json()
        rows = d.get("data") or []
        if not rows:
            break
        all_records.extend(rows)
        if page >= (d.get("TotalPage", 1) or 1):
            break
    return all_records
