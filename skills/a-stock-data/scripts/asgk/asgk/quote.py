"""asgk.quote — 行情层（K线/五档盘口/逐笔/腾讯PE-PB/百度均线K线）。

实现约定：
  - mootdx（K线/五档/逐笔）：TCP 7709 直连，不经网关（暂未迁移）
  - 腾讯（PE/PB/市值/换手）：经网关（tencent 组）
  - 百度（带MA的K线）：经网关（baidu 组，网关用 curl_cffi 指纹出网）
  - tier：日K=R(含今日实时根), 分钟K/五档/逐笔=R, 腾讯行情=R
"""
from __future__ import annotations

import json

from asgk._contract import source
from asgk.client import tdx_client
from asgk.em_proxy import _server_call, em_get

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
@source(tier="R", via="direct", cli="kline", data_type="series")
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

        mootdx 0.11.7 在部分节点返回空日 K 时自动降级到百度日 K；非日线频率
        不做非等价降级。
    """
    client = tdx_client()
    records = _to_records(client.bars(symbol=code, frequency=frequency, offset=offset))
    if records or frequency not in (4, 9):
        return records

    # mootdx 0.11.7 的日 K 在部分节点稳定返回空；日线可安全降级到百度。
    # 分钟/周/月频率不可等价映射，仍按原契约返回 mootdx 结果。
    data = baidu_kline_with_ma(code)
    keys = data.get("keys") or []
    out = []
    for line in (data.get("rows") or [])[-offset:]:
        row = dict(zip(keys, line.split(",")))
        try:
            out.append({
                "open": float(row["open"]), "close": float(row["close"]),
                "high": float(row["high"]), "low": float(row["low"]),
                "vol": float(row["volume"]), "amount": float(row["amount"]),
                "datetime": row["time"],
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


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
@source(tier="R", via="gateway", cli="quote", data_type="kv")
def tencent_quote(codes: list[str]) -> dict[str, dict]:
    """腾讯财经实时行情（PE/PB/市值/换手率/涨跌停/指数/ETF）。

    Args:
        codes: 6位代码列表，也支持指数(000001等)/ETF(510050等)
    Returns:
        {code: {name, price, pe_ttm, pb, mcap_yi(亿), float_mcap_yi, turnover_pct,
        change_pct, limit_up, limit_down, ...}}

    取数路径（§3.4 渐进迁移）：
        1. 优先调能力代理服务端 POST /v1/quote（服务端持有腾讯 URL/GBK/字段映射）
        2. 服务端未配/不可达/报错 → 回退旧 em_get 网关路径（GBK 解码在此）

    Note:
        腾讯返回 GBK 文本需 decode("gbk")。服务端路径已在服务端解码；回退路径在本函数解码。
    """
    # 1. 能力代理服务端（推荐路径）
    data = _server_call("quote", {"codes": list(codes)})
    if data is not None:
        return data
    # 2. 回退：旧 sgw 网关路径（GBK 文本经 em_get 取回，本地解析）
    return _tencent_quote_legacy(codes)


def _tencent_quote_legacy(codes: list[str]) -> dict[str, dict]:
    """回退路径：经 sgw 网关取腾讯 GBK 文本，本地解码+字段映射。

    服务端未部署或不可达时使用。与 capabilities/quote.py._parse_tencent_payload
    的字段映射完全一致（53 字段），保证两条路径返回结构相同。
    """
    prefixed = [_prefix(c) + c for c in codes]
    r = em_get("https://qt.gtimg.cn/q",
               params={"q": ",".join(prefixed)},
               headers={"User-Agent": "Mozilla/5.0"}, timeout=10, tier="R")
    data = r.content.decode("gbk")

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
@source(tier="R", via="gateway", data_type="series")
def baidu_kline_with_ma(code: str, start_time: str = "") -> dict:
    """百度股市通K线（自带 ma5/ma10/ma20 均价，无需本地算）。

    Args:
        code: 6位代码（百度自动识别市场）
        start_time: 起始日 "YYYY-MM-DD"，空=全部
    Returns:
        {keys: [字段名...], rows: ["时间戳,日期,开盘,收盘,成交量,最高,最低,...", ...]}
        ``rows`` 中每项是 CSV 字符串，与 ``keys`` 按下标一一对应。

    Note:
        经网关（baidu 组），网关用 curl_cffi 的 Chrome 协议栈画像出网
        （endpoint 标 egress_client=curl_cffi）。百度会按协议栈画像区分请求，
        普通 urllib/requests 即使带完整 Chrome headers 仍返回 ResultCode=403，
        故网关对该端点用 curl_cffi 指纹出网。
    """
    params = {"all": "1", "isIndex": "false", "isBk": "false", "isBlock": "false",
              "isFutures": "false", "isStock": "true", "newFormat": "1",
              "group": "quotation_kline_ab", "finClientType": "pc",
              "code": code, "start_time": start_time, "ktype": "1"}
    headers = {
        "Accept": "application/vnd.finance-web.v1+json",
        "Origin": "https://gushitong.baidu.com",
        "Referer": "https://gushitong.baidu.com/",
    }
    r = em_get("https://finance.pae.baidu.com/selfselect/getstockquotation",
               params=params, headers=headers, timeout=10, tier="R")
    return _parse_baidu_kline(r, code)


def _parse_baidu_kline(response, code: str) -> dict:
    """解析百度股市通 K 线返回，对风控/异常结构给出清晰错误。

    百度 observed 返回形态：
      - 正常: {"ResultCode":"0", "Result":{"newMarketData":{"keys":[英文...], "headers":[中文...], "marketData":"..."}}}
        （keys 与 headers 同长度，前者英文字段名后者中文；本函数取 keys）
      - 风控: {"ResultCode":"403", "Result":[]}  ← 协议栈画像被拒绝
      - 其它非 0 ResultCode 也按错误处理。
    """
    http_status = response.status_code
    try:
        d = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"百度 K 线返回非 JSON 响应（HTTP {http_status}）"
        ) from exc
    if not isinstance(d, dict):
        raise RuntimeError(f"百度 K 线返回异常结构（HTTP {http_status}）")

    code_str = str(d.get("ResultCode", ""))
    result = d.get("Result")

    # 风控/异常：HTTP 非 200、ResultCode != "0"，或 Result 非 dict。
    if http_status != 200 or code_str != "0" or not isinstance(result, dict):
        transport = f"HTTP {http_status}"
        if code_str == "403":
            hint = f"百度拒绝访问（{transport}，业务码 ResultCode=403）"
            advice = "检查网关 curl_cffi Chrome profile 是否可用，并做单次低频验证"
        elif http_status != 200:
            hint = f"百度返回 HTTP {http_status}（业务码 ResultCode={code_str or '缺失'}）"
            advice = "检查网络和上游状态，不要并发重试"
        elif not code_str:
            hint = "百度返回缺少 ResultCode"
            advice = "检查网络/接口是否变更"
        else:
            hint = f"百度返回异常 ResultCode={code_str}"
            advice = "可稍后重试，或改用其它日K源（注意 mootdx_bars 0.11.7 亦不可用）"
        raise RuntimeError(
            f"baidu_kline_with_ma({code!r}) 取数失败: {hint}。"
            f"原始返回 HTTP={http_status!r}, ResultCode={code_str!r}, "
            f"Result 类型={type(result).__name__}。"
            f"建议：{advice}。"
        )

    md = result.get("newMarketData", {})
    return {"keys": md.get("keys", []), "rows": md.get("marketData", "").split(";")}
