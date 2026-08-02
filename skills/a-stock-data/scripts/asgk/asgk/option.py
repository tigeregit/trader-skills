"""asgk.option — ETF期权层（合约清单/T型报价/希腊字母）。

实现约定：
  - 新浪源直连（hq.sinajs.cn，GBK，必带 Referer），不经网关
  - tier：codes=S(日级慢变), tquote/greeks=R(实时)
"""
from __future__ import annotations

import requests

from asgk._contract import source

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"
SINA_OPT_HDR = {"Referer": "https://stock.finance.sina.com.cn/", "User-Agent": UA}


def _opt_f(x):
    try:
        return float(x)
    except Exception:
        return x


def _sina_opt_list(param: str) -> list:
    """新浪 hq.sinajs.cn 取值（GBK，逗号分隔，去 var hq_str_XXX="..." 壳）。"""
    r = requests.get(f"https://hq.sinajs.cn/list={param}", headers=SINA_OPT_HDR, timeout=10)
    r.encoding = "gbk"
    t = r.text
    return t.split('"')[1].split(",") if '"' in t else []


@source(tier="S", via="direct")
def sina_option_codes(underlying: str = "510050", call: bool = True) -> dict:
    """ETF期权合约清单。

    Args:
        underlying: 510050/510300/588000/510500
        call: True认购/False认沽
    Returns:
        {月份YYMM: [合约代码,...]}，第一个 key 即近月。
    """
    cate = {"510050": "50ETF", "510300": "300ETF",
            "588000": "科创50ETF", "510500": "500ETF"}.get(underlying, "50ETF")
    url = ("https://stock.finance.sina.com.cn/futures/api/openapi.php/"
           f"StockOptionService.getStockName?exchange=null&cate={cate}")
    try:
        months = requests.get(url, headers=SINA_OPT_HDR, timeout=10).json()["result"]["data"]["contractMonth"]
    except Exception:
        return {}
    months = [m.replace("-", "")[2:] for m in months[1:]]  # 丢首个，转 YYMM
    flag = "OP_UP_" if call else "OP_DOWN_"
    out = {}
    for m in months:
        codes = [c.replace("CON_OP_", "") for c in _sina_opt_list(f"{flag}{underlying}{m}")
                 if c.startswith("CON_OP_")]
        if codes:
            out[m] = codes
    return out


@source(tier="R", via="direct")
def sina_option_tquote(code: str) -> dict:
    """期权T型报价。

    Returns: bid_vol/bid/last/ask/ask_vol/open_interest(持仓量)/pct/strike(行权价)/
    prev_close/open/limit_up/limit_down/name/amplitude/high/low/volume/amount。
    """
    v = _sina_opt_list(f"CON_OP_{code}")
    if len(v) < 43:
        return {}
    return {"bid_vol": _opt_f(v[0]), "bid": _opt_f(v[1]), "last": _opt_f(v[2]),
        "ask": _opt_f(v[3]), "ask_vol": _opt_f(v[4]), "open_interest": _opt_f(v[5]),
        "pct": _opt_f(v[6]), "strike": _opt_f(v[7]), "prev_close": _opt_f(v[8]),
        "open": _opt_f(v[9]), "limit_up": _opt_f(v[10]), "limit_down": _opt_f(v[11]),
        "name": v[37], "amplitude": _opt_f(v[38]), "high": _opt_f(v[39]),
        "low": _opt_f(v[40]), "volume": _opt_f(v[41]), "amount": _opt_f(v[42])}


@source(tier="R", via="direct")
def sina_option_greeks(code: str) -> dict:
    """期权希腊字母 + 隐含波动率。

    Returns: name/volume/delta/gamma/theta/vega/iv(小数)/high/low/trade_code/strike/last/theory。
    Note: raw[1:4] 是 3 个空串，必须跳过（[raw[0]] + raw[4:]），否则字段错位。
    """
    raw = _sina_opt_list(f"CON_SO_{code}")
    if len(raw) < 16:
        return {}
    v = [raw[0]] + raw[4:]  # ⚠️ 跳过 raw[1:4] 的 3 个空串
    return {"name": v[0], "volume": _opt_f(v[1]), "delta": _opt_f(v[2]),
        "gamma": _opt_f(v[3]), "theta": _opt_f(v[4]), "vega": _opt_f(v[5]),
        "iv": _opt_f(v[6]), "high": _opt_f(v[7]), "low": _opt_f(v[8]),
        "trade_code": v[9], "strike": _opt_f(v[10]), "last": _opt_f(v[11]), "theory": _opt_f(v[12])}
