"""asgk.capital — 资金面/筹码层（融资融券/大宗/股东户数/分红/资金流）。

移植自 ref/a-stock-data SKILL.md §4.1-4.5。按 asgk-contract.md 契约：
  - 4.1-4.4 经 _datacenter（东财 datacenter-web，走网关）
  - 4.5 经 em_get（东财 push2his，走网关）
  - @source 档位：S(日级)/L(季度)/P(历史定稿)
"""
from __future__ import annotations

from asgk._contract import source
from asgk._datacenter import datacenter as _datacenter
from asgk.em_proxy import em_get

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


@source(tier="S", via="gateway")
def margin_trading(code: str, page_size: int = 30) -> list[dict]:
    """融资融券明细（日级）。

    Returns:
        [{date, rzye(融资余额,元), rzmre(融资买入), rqye(融券余额,元), rzrqye(合计)}, ...]
    """
    data = _datacenter("RPTA_WEB_RZRQ_GGMX", filter_str=f'(SCODE="{code}")',
                       page_size=page_size, sort_columns="DATE", sort_types="-1")
    return [{
        "date": str(row.get("DATE", ""))[:10],
        "rzye": row.get("RZYE", 0), "rzmre": row.get("RZMRE", 0),
        "rzche": row.get("RZCHE", 0), "rqye": row.get("RQYE", 0),
        "rqmcl": row.get("RQMCL", 0), "rqchl": row.get("RQCHL", 0),
        "rzrqye": row.get("RZRQYE", 0),
    } for row in data]


@source(tier="S", via="gateway")
def block_trade(code: str, page_size: int = 20) -> list[dict]:
    """大宗交易记录（日级）。

    Returns:
        [{date, price, close, premium_pct(溢价率), vol, amount, buyer, seller}, ...]
    """
    data = _datacenter("RPT_DATA_BLOCKTRADE", filter_str=f'(SECURITY_CODE="{code}")',
                       page_size=page_size, sort_columns="TRADE_DATE", sort_types="-1")
    rows = []
    for row in data:
        close = row.get("CLOSE_PRICE") or 0
        deal_price = row.get("DEAL_PRICE") or 0
        premium = ((deal_price / close - 1) * 100) if close else 0
        rows.append({
            "date": str(row.get("TRADE_DATE", ""))[:10], "price": deal_price,
            "close": close, "premium_pct": round(premium, 2),
            "vol": row.get("DEAL_VOLUME", 0), "amount": row.get("DEAL_AMT", 0),
            "buyer": row.get("BUYER_NAME", ""), "seller": row.get("SELLER_NAME", ""),
        })
    return rows


@source(tier="L", via="gateway")
def holder_num_change(code: str, page_size: int = 10) -> list[dict]:
    """股东户数变化（季度级）。

    Returns:
        [{date, holder_num, change_num, change_ratio(环比%), avg_shares(户均持股)}, ...]

    Note:
        ⚠️ 上游 ref 的 reportName="RPT_HOLDERNUMLATEST" 实测返回的是融资融券字段
        （非股东户数），疑似 reportName 失效或变更。移植忠实于 ref，待 P4 实测时
        校正正确 reportName（可能为 RPT_F10_EH_HOLDERNUM 之类）。
    """
    data = _datacenter("RPT_HOLDERNUMLATEST", filter_str=f'(SECURITY_CODE="{code}")',
                       page_size=page_size, sort_columns="END_DATE", sort_types="-1")
    return [{
        "date": str(row.get("END_DATE", ""))[:10],
        "holder_num": row.get("HOLDER_NUM", 0),
        "change_num": row.get("HOLDER_NUM_CHANGE", 0),
        "change_ratio": row.get("HOLDER_NUM_RATIO", 0),
        "avg_shares": row.get("AVG_FREE_SHARES", 0),
    } for row in data]


@source(tier="P", via="gateway")
def dividend_history(code: str, page_size: int = 20) -> list[dict]:
    """分红送转历史（发布即定稿）。

    Returns:
        [{date(除权除息日), bonus_rmb(每股派息税前), transfer_ratio(每10股转增),
          bonus_ratio(每10股送股), plan(进度)}, ...]
    """
    data = _datacenter("RPT_SHAREBONUS_DET", filter_str=f'(SECURITY_CODE="{code}")',
                       page_size=page_size, sort_columns="EX_DIVIDEND_DATE", sort_types="-1")
    return [{
        "date": str(row.get("EX_DIVIDEND_DATE", ""))[:10],
        "bonus_rmb": row.get("PRETAX_BONUS_RMB", 0),
        "transfer_ratio": row.get("TRANSFER_RATIO", 0),
        "bonus_ratio": row.get("BONUS_RATIO", 0),
        "plan": row.get("ASSIGN_PROGRESS", ""),
    } for row in data]


@source(tier="S", via="gateway", cli="fundflow")
def stock_fund_flow_120d(code: str) -> list[dict]:
    """个股资金流（日级，最近120个交易日）。

    Returns:
        [{date, main_net(主力净流入,元), small_net, mid_net, large_net, super_net}, ...]
    """
    market_code = 1 if code.startswith("6") else 0
    params = {
        "secid": f"{market_code}.{code}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "lmt": "120",
    }
    r = em_get("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
               params=params, headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
               timeout=15, tier="S")
    rows = []
    for line in r.json().get("data", {}).get("klines", []):
        parts = line.split(",")
        if len(parts) >= 7:
            rows.append({
                "date": parts[0],
                "main_net": float(parts[1]) if parts[1] != "-" else 0,
                "small_net": float(parts[2]) if parts[2] != "-" else 0,
                "mid_net": float(parts[3]) if parts[3] != "-" else 0,
                "large_net": float(parts[4]) if parts[4] != "-" else 0,
                "super_net": float(parts[5]) if parts[5] != "-" else 0,
            })
    return rows
