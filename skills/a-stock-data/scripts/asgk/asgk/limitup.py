"""asgk.limitup — 打板层（涨停/炸板/跌停池 + 题材情绪）。

移植自 ref/a-stock-data SKILL.md §8.1-8.3。按 asgk-contract.md 契约：
  - 东财四池走 push2ex（经网关），盘中R/盘后S
  - 同花顺涨停揭秘走 data.10jqka（经网关），R
  - 情绪由四池组合计算，R
"""
from __future__ import annotations

from datetime import datetime

import requests

from asgk._contract import source
from asgk.em_proxy import em_get

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"
ZTB_UT = "7eea3edcaed734bea9cbfc24409ed989"
_EM_POOL_URLS = {
    "getTopicZTPool": "https://push2ex.eastmoney.com/getTopicZTPool",
    "getTopicZBPool": "https://push2ex.eastmoney.com/getTopicZBPool",
    "getTopicDTPool": "https://push2ex.eastmoney.com/getTopicDTPool",
    "getYesterdayZTPool": "https://push2ex.eastmoney.com/getYesterdayZTPool",
}


def _fmt_zt_time(t) -> str:
    """涨停板时间整数 → HH:MM:SS（92500 → 09:25:00）。"""
    s = str(t).zfill(6)
    return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"


def _em_zt_api(endpoint: str, sort: str, date: str) -> list[dict]:
    """东财涨停板通用请求（push2ex，经网关）。data 为 null = 非交易日。"""
    try:
        url = _EM_POOL_URLS[endpoint]
    except KeyError as exc:
        raise ValueError(f"未分类的东财涨停池端点: {endpoint}") from exc
    r = em_get(url,
               params={"ut": ZTB_UT, "dpt": "wz.ztzt", "Pageindex": 0,
                       "pagesize": 10000, "sort": sort, "date": date},
               headers={"Referer": "https://quote.eastmoney.com/"}, timeout=10, tier="R")
    return (r.json().get("data") or {}).get("pool") or []


@source(tier="R", via="gateway")
def em_zt_pool(date: str) -> list[dict]:
    """涨停池。date=YYYYMMDD（交易日）。

    Returns: 每只含 code/name/price/pct/limit_days(连板数)/seal_fund(封板资金,元)/
    break_times(炸板次数)/industry/zt_stat(N天M板) 等。
    """
    out = []
    for p in _em_zt_api("getTopicZTPool", "fbt:asc", date):
        zttj = p.get("zttj") or {}
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
            "pct": round(p["zdp"], 2), "amount": p["amount"], "float_cap": p["ltsz"],
            "turnover": round(p["hs"], 2), "limit_days": p["lbc"],
            "first_seal": _fmt_zt_time(p["fbt"]), "last_seal": _fmt_zt_time(p["lbt"]),
            "seal_fund": p["fund"], "break_times": p["zbc"], "industry": p.get("hybk", ""),
            "zt_stat": f'{zttj.get("days","?")}天{zttj.get("ct","?")}板'})
    return out


@source(tier="R", via="gateway")
def em_zb_pool(date: str) -> list[dict]:
    """炸板池（涨停后开板）。"""
    out = []
    for p in _em_zt_api("getTopicZBPool", "fbt:asc", date):
        zttj = p.get("zttj") or {}
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
            "limit_price": p["ztp"] / 1000, "pct": round(p["zdp"], 2),
            "turnover": round(p["hs"], 2), "first_seal": _fmt_zt_time(p["fbt"]),
            "break_times": p["zbc"], "amplitude": round(p["zf"], 2),
            "speed": round(p["zs"], 2), "industry": p.get("hybk", ""),
            "zt_stat": f'{zttj.get("days","?")}天{zttj.get("ct","?")}板'})
    return out


@source(tier="R", via="gateway")
def em_dt_pool(date: str) -> list[dict]:
    """跌停池。"""
    out = []
    for p in _em_zt_api("getTopicDTPool", "fund:asc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
            "pct": round(p["zdp"], 2), "turnover": round(p["hs"], 2), "pe": p.get("pe"),
            "seal_fund": p["fund"], "last_seal": _fmt_zt_time(p["lbt"]),
            "board_amount": p.get("fba"), "dt_days": p.get("days"),
            "open_times": p.get("oc"), "industry": p.get("hybk", "")})
    return out


@source(tier="S", via="gateway")
def em_yzt_pool(date: str) -> list[dict]:
    """昨日涨停池（昨涨停今表现，算晋级率/赚钱效应）。"""
    out = []
    for p in _em_zt_api("getYesterdayZTPool", "zs:desc", date):
        zttj = p.get("zttj") or {}
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
            "pct": round(p["zdp"], 2), "turnover": round(p["hs"], 2),
            "amplitude": round(p["zf"], 2), "speed": round(p["zs"], 2),
            "y_first_seal": _fmt_zt_time(p["yfbt"]), "y_limit_days": p["ylbc"],
            "industry": p.get("hybk", ""),
            "zt_stat": f'{zttj.get("days","?")}天{zttj.get("ct","?")}板'})
    return out


@source(tier="R", via="gateway")
def ths_limit_up_pool(date: str) -> list[dict]:
    """同花顺涨停揭秘（涨停原因题材 + 封板成功率 + 板型）。date=YYYYMMDD。

    Note: data.10jqka.com.cn 经网关（同花顺组）。
    """
    r = em_get("https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool",
               params={"page": 1, "limit": 200,
                       "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004",
                       "filter": "HS,GEM2STAR", "order_field": "330324",
                       "order_type": "0", "date": date},
               headers={"User-Agent": UA}, timeout=10, tier="R")
    info = (r.json().get("data") or {}).get("info", []) or []
    out = []
    for it in info:
        ft = it.get("first_limit_up_time")
        out.append({"code": it.get("code"), "name": it.get("name"),
            "price": it.get("latest"), "pct": it.get("change_rate"),
            "reason": it.get("reason_type", ""), "board_type": it.get("limit_up_type", ""),
            "seal_rate": it.get("limit_up_suc_rate"), "break_times": it.get("open_num") or 0,
            "seal_amount": it.get("order_amount"), "high_days": it.get("high_days", ""),
            "first_time": datetime.fromtimestamp(int(ft)).strftime("%H:%M:%S") if ft else "",
            "is_again": it.get("is_again_limit")})
    return out


@source(tier="R", via="gateway")
def limit_up_sentiment(date: str) -> dict:
    """打板情绪温度计：连板梯队 + 炸板率 + 涨跌停对比。"""
    zt, zb, dt = em_zt_pool(date), em_zb_pool(date), em_dt_pool(date)
    ladder: dict[int, int] = {}
    for s in zt:
        ladder[s["limit_days"]] = ladder.get(s["limit_days"], 0) + 1
    zt_n, zb_n = len(zt), len(zb)
    return {"date": date, "zt_count": zt_n, "zb_count": zb_n, "dt_count": len(dt),
        "break_rate": round(zb_n / (zt_n + zb_n) * 100, 1) if (zt_n + zb_n) else 0,
        "max_height": max((s["limit_days"] for s in zt), default=0),
        "ladder": dict(sorted(ladder.items()))}
