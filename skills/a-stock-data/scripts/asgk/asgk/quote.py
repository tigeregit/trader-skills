"""asgk.quote — 行情层（K线/五档盘口/逐笔/腾讯PE-PB/百度均线K线）。

移植自 ref/a-stock-data SKILL.md §1.1-1.3。按 asgk-contract.md 契约：
  - mootdx（K线/五档/逐笔）：TCP 7709 直连，不经网关
  - 腾讯（PE/PB/市值/换手）：HTTP 直连，不经网关
  - 百度（带MA的K线）：HTTP 直连，不经网关
  - tier：日K=R(含今日实时根), 分钟K/五档/逐笔=R, 腾讯行情=R
"""
from __future__ import annotations

import urllib.request

import requests

from asgk._contract import source
from asgk.client import tdx_client

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


def _to_records(df) -> list[dict]:
    """mootdx DataFrame → list[dict]（兼容契约结构化返回）。"""
    if df is None or len(df) == 0:
        return []
    return df.to_dict("records")


def _prefix(code: str) -> str:
    """6位代码 → 市场前缀（sh/sz/bj）。"""
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith("8"):
        return "bj"
    return "sz"


# ── mootdx K线 ─────────────────────────────────────────────────
@source(tier="R", via="direct", cli="kline")
def mootdx_bars(code: str, frequency: int = 9, offset: int = 100) -> list[dict]:
    """mootdx K线数据（不复权原始价）。

    Args:
        code: 6位代码
        frequency: 0=5分 1=15分 2=30分 3=60分 4=日线 5=周 6=月 8=1分 9=日线(默认)
        offset: 取最近 N 根
    Returns:
        [{open, close, high, low, vol, amount, datetime}, ...]
    Note:
        ⚠️ 参数名是 frequency 不是 category（传 category 会被静默吞掉退化成日线）。
        返回不复权原始价，跨除权日需自行复权或改用百度K线。

        ⚠️ 2026-07 实测部分 mootdx 版本 bars 返回 0 条（finance 正常，疑似 bars 接口
        兼容性问题）。日K可靠替代用 `baidu_kline_with_ma`（自带均线），或腾讯行情取实时价。
        待 P4 实测时定位 mootdx bars 正确调用方式。
    """
    client = tdx_client()
    return _to_records(client.bars(symbol=code, frequency=frequency, offset=offset))


# ── mootdx 五档盘口 ─────────────────────────────────────────────
@source(tier="R", via="direct")
def mootdx_quotes(codes: list[str]) -> list[dict]:
    """mootdx 实时五档盘口（46 字段）。

    Args:
        codes: 6位代码列表
    Returns:
        每只含 price/open/high/low/last_close/bid1~5/ask1~5/bid_vol1~5/ask_vol1~5/vol/amount/servertime。
    """
    client = tdx_client()
    return _to_records(client.quotes(symbol=codes))


# ── mootdx 逐笔成交 ─────────────────────────────────────────────
@source(tier="R", via="direct")
def mootdx_transaction(code: str, date: str | None = None) -> list[dict]:
    """mootdx 逐笔成交（非交易时间返回空）。

    Args:
        code: 6位代码
        date: "YYYYMMDD"，None=当天
    Returns:
        [{time, price, vol, num, buyorsell(0买/1卖/2中性)}, ...]
    """
    client = tdx_client()
    return _to_records(client.transaction(symbol=code, date=date) if date else client.transaction(symbol=code))


# ── 腾讯 PE/PB/市值 ─────────────────────────────────────────────
@source(tier="R", via="direct", cli="quote")
def tencent_quote(codes: list[str]) -> dict[str, dict]:
    """腾讯财经实时行情（PE/PB/市值/换手率/涨跌停/指数/ETF）。

    Args:
        codes: 6位代码列表，也支持指数(000001等)/ETF(510050等)
    Returns:
        {code: {name, price, pe_ttm, pb, mcap_yi(亿), float_mcap_yi, turnover_pct,
        change_pct, limit_up, limit_down, ...}}
    """
    prefixed = [_prefix(c) + c for c in codes]
    req = urllib.request.Request("https://qt.gtimg.cn/q=" + ",".join(prefixed))
    req.add_header("User-Agent", "Mozilla/5.0")
    data = urllib.request.urlopen(req, timeout=10).read().decode("gbk")

    result = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key[2:]
        result[code] = {
            "name": vals[1],
            "price": float(vals[3]) if vals[3] else 0,
            "last_close": float(vals[4]) if vals[4] else 0,
            "open": float(vals[5]) if vals[5] else 0,
            "change_amt": float(vals[31]) if vals[31] else 0,
            "change_pct": float(vals[32]) if vals[32] else 0,
            "high": float(vals[33]) if vals[33] else 0,
            "low": float(vals[34]) if vals[34] else 0,
            "amount_wan": float(vals[37]) if vals[37] else 0,
            "turnover_pct": float(vals[38]) if vals[38] else 0,
            "pe_ttm": float(vals[39]) if vals[39] else 0,
            "amplitude_pct": float(vals[43]) if vals[43] else 0,
            "mcap_yi": float(vals[44]) if vals[44] else 0,
            "float_mcap_yi": float(vals[45]) if vals[45] else 0,
            "pb": float(vals[46]) if vals[46] else 0,
            "limit_up": float(vals[47]) if vals[47] else 0,
            "limit_down": float(vals[48]) if vals[48] else 0,
            "vol_ratio": float(vals[49]) if vals[49] else 0,
            "pe_static": float(vals[52]) if vals[52] else 0,
        }
    return result


# ── 百度带MA的K线 ───────────────────────────────────────────────
@source(tier="R", via="direct")
def baidu_kline_with_ma(code: str, start_time: str = "") -> dict:
    """百度股市通K线（自带 ma5/ma10/ma20 均价，无需本地算）。

    Args:
        code: 6位代码（百度自动识别市场）
        start_time: 起始日 "YYYY-MM-DD"，空=全部
    Returns:
        {keys: [字段名...], rows: ["时间,开,收,高,低,量,额,ma5,ma10,ma20", ...]}
    """
    r = requests.get("https://finance.pae.baidu.com/selfselect/getstockquotation",
                     params={"all": "1", "isIndex": "false", "isBk": "false", "isBlock": "false",
                             "isFutures": "false", "isStock": "true", "newFormat": "1",
                             "group": "quotation_kline_ab", "finClientType": "pc",
                             "code": code, "start_time": start_time, "ktype": "1"},
                     headers={"User-Agent": "Mozilla/5.0",
                              "Accept": "application/vnd.finance-web.v1+json",
                              "Origin": "https://gushitong.baidu.com",
                              "Referer": "https://gushitong.baidu.com/"},
                     timeout=10)
    result = r.json().get("Result", {})
    md = result.get("newMarketData", {})
    return {"keys": md.get("keys", []), "rows": md.get("marketData", "").split(";")}
