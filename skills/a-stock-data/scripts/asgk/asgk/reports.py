"""asgk.reports — 研报层（东财研报 + 同花顺一致预期EPS）。

实现约定：
  - 东财研报经网关，P档；同花顺EPS经网关，S档
  - 返回结构化 list[dict]
"""
from __future__ import annotations

from io import StringIO

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
        研报 record 列表，包含 title/publishDate/orgSName/infoCode/
        predictThisYearEps/emRatingName/indvInduName 等字段。
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


@source(tier="S", via="gateway")
def ths_eps_forecast(code: str) -> list[dict]:
    """同花顺机构一致预期 EPS（解析 HTML 表格）。

    Args:
        code: 6位股票代码
    Returns:
        年度一致预期 EPS 列表，字段含 年度/预测机构数/最小值/均值/最大值。
        "均值" = 机构一致预期 EPS。预测机构数 < 3 要谨慎。
    Note:
        经网关（basic.10jqka.com.cn，同花顺组）。用 pandas read_html 解析（pandas
        已随 mootdx 依赖引入，非新增重依赖）。
    """
    import pandas as pd
    r = em_get(f"https://basic.10jqka.com.cn/new/{code}/worth.html",
               headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                        "Referer": "https://basic.10jqka.com.cn/"},
               timeout=15, tier="S")
    r.encoding = "gbk"
    dfs = pd.read_html(StringIO(r.text))
    for df in dfs:
        cols = [str(c) for c in df.columns]
        if any("每股收益" in c or "均值" in c for c in cols):
            return df.to_dict("records")
    return (dfs[0].to_dict("records") if dfs else [])
