"""asgk.valuation_hist — 估值历史层（全市场 PE / PB 历史）。

移植自 akshare stock_market_pe_lg / stock_market_pb_lg（snapshot fcdbf25）。
乐咕源经网关（legulegu 组，caller 模式透传 CSRF），需 token + CSRF cookie + Referer。
  - token：纯 Python md5(当日日期)，与 akshare 的 JS 版 hash_code 输出一致
  - CSRF：先 GET 页面拿 <meta name="_csrf">，以 X-CSRF-Token 头 + Cookie 请求
  - caller 模式：调用方各自跑 CSRF 第一步，token 当天有效；网关不持有 cookie
  - @source 档位：L（历史数据，日级）
"""
from __future__ import annotations

from datetime import datetime
from hashlib import md5

from lxml import html

from asgk._contract import source
from asgk.em_proxy import em_get

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"

# PE: marketId 映射（科创版走单独 URL，暂不支持，见 notes）
_PE_MARKET = {"上证": "1", "深证": "2", "创业板": "4"}
_PE_PAGE = {"上证": "https://legulegu.com/stockdata/shanghaiPE",
            "深证": "https://legulegu.com/stockdata/shenzhenPE",
            "创业板": "https://legulegu.com/stockdata/cybPE"}

# PB: indexCode 映射（四市场统一 URL）
_PB_MARKET = {"上证": "1", "深证": "2", "创业板": "4", "科创版": "7"}
_PB_PAGE = {"上证": "https://legulegu.com/stockdata/shanghaiPB",
            "深证": "https://legulegu.com/stockdata/shenzhenPB",
            "创业板": "https://legulegu.com/stockdata/cybPB",
            "科创版": "https://legulegu.com/stockdata/ke-chuang-ban-pb"}


def _legu_token() -> str:
    """乐咕请求 token = md5(当日日期 ISO 字符串)。

    与 akshare 的 JS 版 hash_code（py_mini_racer 执行）输出完全一致
    （2026-07-31 真机验证：ffed2e42417825d0315ed5c27b9eeade），无需 vendor JS。
    """
    return md5(datetime.now().date().isoformat().encode()).hexdigest()


def _legu_csrf_from(resp) -> tuple[dict, str]:
    """从已获取的页面响应解析 CSRF token + cookie（caller 模式）。

    Args:
        resp: em_get 返回的页面 Response
    Returns:
        (headers, cookie_str)：headers 含 X-CSRF-Token，cookie_str 是拼好的 Cookie 头值
    """
    tree = html.fromstring(resp.text)
    nodes = tree.xpath('//meta[@name="_csrf"]/@content')
    if not nodes:
        raise ValueError("未找到乐咕 CSRF token")
    cookie_str = "; ".join(f"{k}={v}" for k, v in resp.cookies.items()) if resp.cookies else ""
    return ({"User-Agent": UA, "X-CSRF-Token": nodes[0]}, cookie_str)


@source(tier="L", via="gateway")
def market_pe_lg(market: str = "上证") -> list[dict]:
    """全市场市盈率历史（乐咕）。

    移植自 akshare stock_market_pe_lg。

    Args:
        market: 市场关键词，上证/深证/创业板
    Returns:
        [{date, close(收盘指数), pe(平均市盈率)}, ...]
    Note:
        经网关（legulegu 组，caller 模式）：调用方跑 CSRF 第一步，token+cookie 透传。
    """
    if market not in _PE_MARKET:
        raise ValueError(f"PE market 取值: 上证/深证/创业板（科创版走单独URL，暂不支持），得到: {market!r}")
    page = em_get(_PE_PAGE[market], headers={"User-Agent": UA}, timeout=15, tier="L")
    headers, cookie_str = _legu_csrf_from(page)
    if cookie_str:
        headers["Cookie"] = cookie_str
    r = em_get("https://legulegu.com/api/stock-data/market-pe",
               params={"token": _legu_token(), "marketId": _PE_MARKET[market]},
               headers=headers, timeout=15, tier="L")
    data = r.json().get("data", [])
    return [{"date": str(row.get("date", ""))[:10],
             "close": row.get("close"), "pe": row.get("pe")} for row in data]


@source(tier="L", via="gateway")
def market_pb_lg(market: str = "上证") -> list[dict]:
    """全市场市净率历史（乐咕）。

    移植自 akshare stock_market_pb_lg。

    Args:
        market: 市场关键词，上证/深证/创业板/科创版
    Returns:
        [{date, close(收盘指数), pb(平均市净率), add_pb(附加市净率)}, ...]
    Note:
        经网关（legulegu 组，caller 模式）：调用方跑 CSRF 第一步，token+cookie 透传。
    """
    if market not in _PB_MARKET:
        raise ValueError(f"PB market 取值: 上证/深证/创业板/科创版，得到: {market!r}")
    page = em_get(_PB_PAGE[market], headers={"User-Agent": UA}, timeout=15, tier="L")
    headers, cookie_str = _legu_csrf_from(page)
    if cookie_str:
        headers["Cookie"] = cookie_str
    r = em_get("https://legulegu.com/api/stockdata/index-basic-pb",
               params={"token": _legu_token(), "indexCode": _PB_MARKET[market]},
               headers=headers, timeout=15, tier="L")
    data = r.json().get("data", [])
    return [{"date": str(row.get("date", ""))[:10],
             "close": row.get("close"), "pb": row.get("pb"),
             "add_pb": row.get("addPb")} for row in data]
