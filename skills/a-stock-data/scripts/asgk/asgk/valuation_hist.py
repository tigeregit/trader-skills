"""asgk.valuation_hist — 估值历史层（全市场 PE / PB 历史）。

移植自 akshare stock_market_pe_lg / stock_market_pb_lg（snapshot fcdbf25）。
乐咕源（直连，[§7 决策10] 验证无风控），需 token + CSRF cookie + Referer。
  - token：纯 Python md5(当日日期)，与 akshare 的 JS 版 hash_code 输出一致
  - CSRF：先 GET 页面拿 <meta name="_csrf">，以 X-CSRF-Token 头 + cookie 请求
  - 进程内自律限流（复用 em_proxy._direct_throttle 模式）
  - @source 档位：L（历史数据，日级）
"""
from __future__ import annotations

from datetime import datetime
from hashlib import md5

import requests
from lxml import html

from asgk._contract import source

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


def _legu_csrf(page_url: str) -> tuple[dict, dict]:
    """GET 乐咕页面拿 CSRF token + cookie。

    Returns:
        (headers, cookies)：headers 含 X-CSRF-Token，cookies 是 session cookie
    """
    r = requests.get(page_url, headers={"User-Agent": UA}, timeout=15)
    tree = html.fromstring(r.text)
    nodes = tree.xpath('//meta[@name="_csrf"]/@content')
    if not nodes:
        raise ValueError(f"未找到乐咕 CSRF token（页面: {page_url}）")
    return ({"User-Agent": UA, "X-CSRF-Token": nodes[0], "Referer": page_url},
            dict(r.cookies))


@source(tier="L", via="direct")
def market_pe_lg(market: str = "上证") -> list[dict]:
    """全市场市盈率历史（乐咕）。

    移植自 akshare stock_market_pe_lg。

    Args:
        market: 市场关键词，上证/深证/创业板
    Returns:
        [{date, close(收盘指数), pe(平均市盈率)}, ...]
    """
    if market not in _PE_MARKET:
        raise ValueError(f"PE market 取值: 上证/深证/创业板（科创版走单独URL，暂不支持），得到: {market!r}")
    headers, cookies = _legu_csrf(_PE_PAGE[market])
    r = requests.get("https://legulegu.com/api/stock-data/market-pe",
                     params={"token": _legu_token(), "marketId": _PE_MARKET[market]},
                     headers=headers, cookies=cookies, timeout=15)
    data = r.json().get("data", [])
    return [{"date": str(row.get("date", ""))[:10],
             "close": row.get("close"), "pe": row.get("pe")} for row in data]


@source(tier="L", via="direct")
def market_pb_lg(market: str = "上证") -> list[dict]:
    """全市场市净率历史（乐咕）。

    移植自 akshare stock_market_pb_lg。

    Args:
        market: 市场关键词，上证/深证/创业板/科创版
    Returns:
        [{date, close(收盘指数), pb(平均市净率), add_pb(附加市净率)}, ...]
    """
    if market not in _PB_MARKET:
        raise ValueError(f"PB market 取值: 上证/深证/创业板/科创版，得到: {market!r}")
    headers, cookies = _legu_csrf(_PB_PAGE[market])
    r = requests.get("https://legulegu.com/api/stockdata/index-basic-pb",
                     params={"token": _legu_token(), "indexCode": _PB_MARKET[market]},
                     headers=headers, cookies=cookies, timeout=15)
    data = r.json().get("data", [])
    return [{"date": str(row.get("date", ""))[:10],
             "close": row.get("close"), "pb": row.get("pb"),
             "add_pb": row.get("addPb")} for row in data]
