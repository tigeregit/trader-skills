"""asgk.signal — 信号层（热点/北向/板块/资金流/龙虎榜/解禁/行业）。

实现约定：
  - 东财端点经 em_get 走网关；同花顺热点走网关(10jqka组)；北向(data.hexin.cn)经网关
  - 返回结构化 dict/list，表格数据统一为 list[dict]
  - @source 声明档位：S(日级定稿)/R(实时)
"""
from __future__ import annotations

from datetime import datetime, timedelta

from asgk._contract import source
from asgk._datacenter import datacenter as _datacenter
from asgk.em_proxy import _server_call, em_get

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


# ── 3.1 同花顺热点 ──────────────────────────────────────────────
@source(tier="S", via="gateway", data_type="table")
def ths_hot_reason(date: str | None = None) -> list[dict]:
    """同花顺当日强势股 + 题材归因 reason tags。

    Args:
        date: "YYYY-MM-DD"，None=今天
    Returns:
        强势股列表，字段：code/name/reason(题材归因)/close/zhangfu(涨幅%)/huanshou(换手率%)/chengjiaoe 等。
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    url = (f"http://zx.10jqka.com.cn/event/api/getharden/"
           f"date/{date}/orderby/date/orderway/desc/charset/GBK/")
    r = em_get(url, headers={"User-Agent": UA}, timeout=10, tier="S")
    data = r.json()
    if data.get("errocode", 0) != 0:
        raise RuntimeError(f"同花顺热点错误: {data.get('errormsg', '')}")
    return data.get("data") or []


# ── 3.2 北向资金（data.hexin.cn，经网关）────────────────────────
# 注：data.hexin.cn 属同花顺系，与 .10jqka.com.cn 共用 hexin-v 风控，一处被封会
# 连累 zx.10jqka(热点) 等全系成片失联，故经网关串行限流。原"直连不封IP"注释有误。
@source(tier="R", via="gateway", data_type="table")
def hsgt_realtime() -> list[dict]:
    """沪深股通当日实时分钟流向（含集合竞价 09:10-15:00）。

    Returns:
        [{time, hgt_yi(沪股通累计净买入,亿元), sgt_yi(深股通累计净买入,亿元)}, ...]
    Note:
        深股通(sgt)自2024-08披露收紧，常只回零星点；hgt 可靠。权威北向用 HKEX 官方日统计。
        data.hexin.cn 经网关（同花顺限流组）；Host 由 requests 按 URL 自动设置。
    """
    url = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
    r = em_get(url, headers={"User-Agent": UA, "Referer": "https://data.hexin.cn/"}, timeout=10, tier="R")
    d = r.json()
    times = d.get("time", [])
    hgt = d.get("hgt", [])
    sgt = d.get("sgt", [])
    n = len(times)
    return [
        {"time": times[i],
         "hgt_yi": hgt[i] if i < len(hgt) else None,
         "sgt_yi": sgt[i] if i < len(sgt) else None}
        for i in range(n)
    ]


# ── 3.3 个股板块归属 ────────────────────────────────────────────
@source(tier="S", via="gateway", cli="block", data_type="kv")
def eastmoney_concept_blocks(code: str) -> dict:
    """个股所属板块/概念归属（东财 slist，一次请求拿全）。

    Returns:
        {total, boards: [{name, code(BK码), change_pct, lead_stock}], concept_tags: [板块名...]}

    取数路径（§3.4）：优先调 concept_blocks 能力（服务端持 secid/diff 解析），回退旧路径。
    """
    data = _server_call("concept_blocks", {"code": code})
    if data is not None:
        return data
    return _eastmoney_concept_blocks_legacy(code)


def _eastmoney_concept_blocks_legacy(code: str) -> dict:
    """回退路径：经 sgw 网关取 push2 slist/get，本地 diff 解析。"""
    market_code = 1 if code.startswith("6") else 0
    params = {
        "fltt": "2", "invt": "2",
        "secid": f"{market_code}.{code}",
        "spt": "3", "pi": "0", "pz": "200", "po": "1",
        "fields": "f12,f14,f3,f128",
    }
    r = em_get("https://push2.eastmoney.com/api/qt/slist/get",
               params=params, headers={"Referer": "https://quote.eastmoney.com/"}, timeout=15, tier="S")
    d = r.json()
    diff = (d.get("data") or {}).get("diff") or {}
    items = diff.values() if isinstance(diff, dict) else diff
    boards = [{
        "name": it.get("f14", ""),
        "code": it.get("f12", ""),
        "change_pct": it.get("f3", ""),
        "lead_stock": it.get("f128", ""),
    } for it in items]
    return {"total": len(boards), "boards": boards, "concept_tags": [b["name"] for b in boards]}


# ── 3.4 个股分钟资金流 ──────────────────────────────────────────
@source(tier="R", via="gateway", data_type="table")
def eastmoney_fund_flow_minute(code: str) -> list[dict]:
    """个股资金流向（分钟级，当日盘中）。

    Returns:
        [{time, main_net, small_net, mid_net, large_net, super_net}, ...]（单位：元）
    """
    secid = f"1.{code}" if code.startswith("6") else f"0.{code}"
    params = {"secid": secid, "klt": 1, "fields1": "f1,f2,f3,f7",
              "fields2": "f51,f52,f53,f54,f55,f56,f57"}
    r = em_get("https://push2.eastmoney.com/api/qt/stock/fflow/kline/get",
               params=params, headers={"Referer": "https://quote.eastmoney.com/"}, timeout=10, tier="R")
    rows = []
    for line in r.json().get("data", {}).get("klines", []):
        parts = line.split(",")
        if len(parts) >= 6:
            rows.append({
                "time": parts[0], "main_net": float(parts[1]),
                "small_net": float(parts[2]), "mid_net": float(parts[3]),
                "large_net": float(parts[4]), "super_net": float(parts[5]),
            })
    return rows


# ── 3.5 龙虎榜席位 ──────────────────────────────────────────────
@source(tier="S", via="gateway", data_type="kv")
def dragon_tiger_board(code: str, trade_date: str, look_back: int = 30) -> dict:
    """个股龙虎榜（上榜记录 + 买卖席位 TOP5 + 机构动向）。

    Args:
        code: 6位股票代码
        trade_date: "YYYY-MM-DD"
        look_back: 回看天数
    Returns:
        {records: [...], seats: {buy: [...], sell: [...]}, institution: {...}}
    """
    start = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=look_back)).strftime("%Y-%m-%d")
    # 上榜记录
    data = _datacenter("RPT_DAILYBILLBOARD_DETAILSNEW",
                       filter_str=f"(TRADE_DATE>='{start}')(TRADE_DATE<='{trade_date}')(SECURITY_CODE=\"{code}\")",
                       page_size=50, sort_columns="TRADE_DATE", sort_types="-1")
    records = [{
        "date": str(row.get("TRADE_DATE", ""))[:10],
        "reason": row.get("EXPLANATION", ""),
        "net_buy": round((row.get("BILLBOARD_NET_AMT") or 0) / 10000, 1),
        "turnover": round(float(row.get("TURNOVERRATE") or 0), 2),
    } for row in data]

    seats = {"buy": [], "sell": []}
    if records:
        latest = records[0]["date"]
        for side, report, sort_col in [("buy", "RPT_BILLBOARD_DAILYDETAILSBUY", "BUY"),
                                        ("sell", "RPT_BILLBOARD_DAILYDETAILSSELL", "SELL")]:
            detail = _datacenter(report,
                                 filter_str=f"(TRADE_DATE='{latest}')(SECURITY_CODE=\"{code}\")",
                                 page_size=10, sort_columns=sort_col, sort_types="-1")
            seats[side] = [{
                "name": row.get("OPERATEDEPT_NAME", ""),
                "buy_amt": round((row.get("BUY") or 0) / 10000, 1),
                "sell_amt": round((row.get("SELL") or 0) / 10000, 1),
                "net": round((row.get("NET") or 0) / 10000, 1),
            } for row in detail[:5]]
    return {"records": records, "seats": seats}


# ── 3.6 限售解禁日历 ────────────────────────────────────────────
@source(tier="S", via="gateway", data_type="kv")
def lockup_expiry(code: str, trade_date: str, forward_days: int = 90) -> dict:
    """限售解禁日历（历史解禁 + 未来待解禁）。

    Returns:
        {history: [...], upcoming: [{date, type, shares, able_shares, ratio}, ...]}
    """
    def _parse(rows):
        return [{
            "date": str(row.get("FREE_DATE", ""))[:10],
            "type": row.get("FREE_SHARES_TYPE", ""),
            "shares": row.get("FREE_SHARES", 0),
            "able_shares": row.get("ABLE_FREE_SHARES", 0),
            "ratio": row.get("FREE_RATIO", 0),
        } for row in rows]

    history = _parse(_datacenter("RPT_LIFT_STAGE",
                                 filter_str=f"(SECURITY_CODE=\"{code}\")",
                                 page_size=15, sort_columns="FREE_DATE", sort_types="-1"))
    end_date = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=forward_days)).strftime("%Y-%m-%d")
    upcoming = _parse(_datacenter("RPT_LIFT_STAGE",
                                  filter_str=f"(SECURITY_CODE=\"{code}\")(FREE_DATE>='{trade_date}')(FREE_DATE<='{end_date}')",
                                  page_size=20, sort_columns="FREE_DATE", sort_types="1"))
    return {"history": history, "upcoming": upcoming}


# ── 3.7 行业板块排名 ────────────────────────────────────────────
@source(tier="R", via="gateway", data_type="kv")
def industry_comparison(top_n: int = 20) -> dict:
    """全行业涨跌幅排名（~100 个行业）。

    Returns:
        {top: [...], bottom: [...], total: int}（盘中R实时/盘后S定稿）
    """
    params = {"pn": "1", "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2",
              "fid": "f3", "fs": "m:90+t:2",
              "fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207"}
    r = em_get("https://push2.eastmoney.com/api/qt/clist/get",
               params=params, timeout=15, tier="R")
    items = r.json().get("data", {}).get("diff", [])
    if not items:
        return {"top": [], "bottom": [], "total": 0}
    rows = [{
        "rank": i + 1, "name": it.get("f14", ""), "change_pct": it.get("f3", 0),
        "code": it.get("f12", ""), "up_count": it.get("f104", 0), "down_count": it.get("f105", 0),
        "leader": it.get("f140", ""), "leader_change": it.get("f136", 0),
    } for i, it in enumerate(items)]
    return {"top": rows[:top_n], "bottom": rows[-top_n:], "total": len(rows)}


# ── 3.8 全市场龙虎榜 ────────────────────────────────────────────
@source(tier="S", via="gateway", data_type="kv")
def daily_dragon_tiger(trade_date: str | None = None, min_net_buy: float | None = None) -> dict:
    """全市场龙虎榜（当日所有上榜股票 + 净买额排名）。

    Args:
        trade_date: "YYYY-MM-DD"（默认当日）
        min_net_buy: 净买入下限（万元），None 不过滤
    Returns:
        {date, total_records, stocks: [{code, name, reason, close, change_pct, net_buy_wan, ...}]}
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    data = _datacenter("RPT_DAILYBILLBOARD_DETAILSNEW",
                       filter_str=f"(TRADE_DATE>='{trade_date}')(TRADE_DATE<='{trade_date}')",
                       page_size=500, sort_columns="BILLBOARD_NET_AMT", sort_types="-1")
    if not data:
        return {"date": trade_date, "total_records": 0, "stocks": [],
                "note": "无数据（非交易日或盘后未更新）"}
    stocks = []
    for row in data:
        net_buy = (row.get("BILLBOARD_NET_AMT") or 0) / 10000
        if min_net_buy is not None and net_buy < min_net_buy:
            continue
        stocks.append({
            "code": row.get("SECURITY_CODE", ""), "name": row.get("SECURITY_NAME_ABBR", ""),
            "reason": row.get("EXPLANATION", ""), "close": row.get("CLOSE_PRICE") or 0,
            "change_pct": round(float(row.get("CHANGE_RATE") or 0), 2),
            "net_buy_wan": round(net_buy, 1),
            "buy_wan": round((row.get("BILLBOARD_BUY_AMT") or 0) / 10000, 1),
            "sell_wan": round((row.get("BILLBOARD_SELL_AMT") or 0) / 10000, 1),
            "turnover_pct": round(float(row.get("TURNOVERRATE") or 0), 2),
        })
    return {"date": str(data[0].get("TRADE_DATE", ""))[:10], "total_records": len(stocks), "stocks": stocks}
