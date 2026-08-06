"""asgk.base — 基础数据层（财务快照/F10/个股信息/财报三表）。

实现约定：
  - mootdx 财务快照/F10：TCP 7709 直连，tier=L/P（暂未迁移）
  - 东财个股信息：经网关(push2)，tier=S
  - 新浪财报三表：经网关(sina 组)，tier=L
"""
from __future__ import annotations

from asgk._contract import source
from asgk.client import tdx_client
from asgk.em_proxy import _server_call, em_get

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


@source(tier="L", via="direct")
def mootdx_finance(code: str) -> dict:
    """mootdx 季报财务快照（37 字段）。

    Args:
        code: 6位股票代码
    Returns:
        含 liutongguben/zongguben/eps/bvps/roe/profit/income 等 ~37 字段的 dict。
        字段名见 mootdx 文档（拼音缩写）。

    取数路径（§3.4）：优先调 mootdx 能力（mootdx_type=finance），回退旧路径。
    """
    data = _server_call("mootdx", {"mootdx_type": "finance", "code": code})
    if data is not None:
        return data
    return _mootdx_finance_legacy(code)


def _mootdx_finance_legacy(code: str) -> dict:
    """回退路径：本地 tdx_client 取财务快照。"""
    client = tdx_client()
    df = client.finance(symbol=code)
    # mootdx 返回 1×37 DataFrame，转为 dict
    if df is None or len(df) == 0:
        return {}
    return df.to_dict("records")[0]


@source(tier="P", via="direct", data_type="text")
def mootdx_f10(code: str, name: str = "公司概况") -> str:
    """mootdx F10 公司文本资料（9 大类）。

    Args:
        code: 6位股票代码
        name: 类目，可选：最新提示/公司概况/财务分析/股东研究/股本结构/
              资本运作/业内点评/行业分析/公司大事
    Returns:
        该类目的文本内容（"股东研究"含历史十大股东，可达 16000+ chars）。

    取数路径（§3.4）：优先调 mootdx 能力（mootdx_type=f10），回退旧路径。
    """
    data = _server_call("mootdx", {"mootdx_type": "f10", "code": code, "name": name})
    if data is not None:
        return data
    return _mootdx_f10_legacy(code, name)


def _mootdx_f10_legacy(code: str, name: str = "公司概况") -> str:
    """回退路径：本地 tdx_client 取 F10 文本。"""
    client = tdx_client()
    return client.F10(symbol=code, name=name) or ""


@source(tier="S", via="gateway")
def eastmoney_stock_info(code: str) -> dict:
    """东财个股基本面信息。

    Returns:
        {code, name, industry, total_shares, float_shares, mcap(总市值,元),
         float_mcap(流通市值,元), list_date(YYYYMMDD), price}

    取数路径（§3.4）：优先调 stock_info 能力（服务端持 secid/f字段表），回退旧路径。
    """
    data = _server_call("stock_info", {"code": code})
    if data is not None:
        return data
    return _eastmoney_stock_info_legacy(code)


def _eastmoney_stock_info_legacy(code: str) -> dict:
    """回退路径：经 sgw 网关取 push2 stock/get，本地字段映射。"""
    market_code = 1 if code.startswith("6") else 0
    r = em_get("https://push2.eastmoney.com/api/qt/stock/get",
               params={"fltt": "2", "invt": "2",
                       "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43",
                       "secid": f"{market_code}.{code}"},
               timeout=10, tier="S")
    d = r.json().get("data") or {}
    return {
        "code": d.get("f57", ""), "name": d.get("f58", ""),
        "industry": d.get("f127", ""),
        "total_shares": d.get("f84", 0), "float_shares": d.get("f85", 0),
        "mcap": d.get("f116", 0), "float_mcap": d.get("f117", 0),
        "list_date": str(d.get("f189", "")), "price": d.get("f43", 0),
    }


@source(tier="L", via="gateway")
def sina_financial_report(code: str, report_type: str = "lrb", num: int = 8) -> list[dict]:
    """新浪财报三表。

    Args:
        code: 6位代码
        report_type: "fzb"(资产负债表) / "lrb"(利润表) / "llb"(现金流量表)
        num: 取最近 N 期
    Returns:
        按报告期倒序的记录列表，每期一条 dict：
        {"报告期": "2026-03-31", "<科目>": "<值>", "<科目>_同比": <同比>, ...}

    取数路径（§3.4）：优先调 sina_finance 能力（report_list 解析下沉），回退旧路径。
    """
    data = _server_call("sina_finance", {"code": code, "report_type": report_type, "num": num})
    if data is not None:
        return data
    return _sina_financial_report_legacy(code, report_type, num)


def _sina_financial_report_legacy(code: str, report_type: str = "lrb", num: int = 8) -> list[dict]:
    """回退路径：经 sgw 网关取新浪财报，本地解析 report_list。"""
    prefix = "sh" if code.startswith("6") else "sz"
    r = em_get(
        "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022",
        params={"paperCode": f"{prefix}{code}", "source": report_type,
                "type": "0", "page": "1", "num": str(num)},
        headers={"User-Agent": UA}, timeout=15, tier="L",
    )
    # 新浪结构: result.data.report_list 是「按报告期(如 '20260331')为键」的 dict
    report_list = r.json().get("result", {}).get("data", {}).get("report_list", {}) or {}
    rows = []
    for period in sorted(report_list.keys(), reverse=True)[:num]:
        obj = report_list[period]
        rec = {"报告期": f"{period[:4]}-{period[4:6]}-{period[6:8]}"}
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
