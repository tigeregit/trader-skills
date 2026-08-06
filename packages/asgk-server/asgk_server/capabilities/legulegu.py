"""legulegu 能力 — 乐咕全市场 PE/PB 历史（CSRF 会话）。

把 asgk/valuation_hist.py.market_pe_lg / market_pb_lg 的上游知识下沉到服务端：
  - 三步流（CSRF 会话）：GET 页面 → 解析 <meta name="_csrf"> → 带 cookie + token
    的 GET API。服务端持有 cookie jar，CSRF 两步流在服务端闭环——解决无状态代理
    无法保持会话的核心痛点（这是能力代理相对透明代理的核心收益之一）。
  - token = md5(当日日期 ISO 字符串)（与 akshare JS 版 hash_code 输出一致）
  - PE：marketId 参数（上证=1/深证=2/创业板=4），走 /api/stock-data/market-pe
  - PB：indexCode 参数（上证=1/深证=2/创业板=4/科创版=7），走 /api/stockdata/index-basic-pb

用 lg_type 参数区分（pe / pb）。客户端发 {lg_type, market}，服务端三步流闭环 +
返回结构化历史数据。
"""
from __future__ import annotations

from datetime import datetime
from hashlib import md5
from typing import Any

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"

_PE_MARKET = {"上证": "1", "深证": "2", "创业板": "4"}
_PE_PAGE = {"上证": "https://legulegu.com/stockdata/shanghaiPE",
            "深证": "https://legulegu.com/stockdata/shenzhenPE",
            "创业板": "https://legulegu.com/stockdata/cybPE"}
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


def _get_csrf(ctx: FetchContext, page_url: str) -> tuple[dict, dict] | None:
    """GET 乐咕页面拿 CSRF token + cookie（限流 + 熔断反馈）。

    Returns:
        (headers, cookies)：headers 含 X-CSRF-Token，cookies 是 session cookie。
        失败返回 None（上游不可达/页面结构变更）。
    """
    from lxml import html
    if not ctx.acquire():
        return None
    try:
        r = egress_request("get", ctx.source.egress_client, page_url,
                           headers={"User-Agent": _UA}, timeout=15)
    except Exception:
        ctx.on_network_error()
        return None
    if r.status_code in (403, 429):
        ctx.on_failure(status=r.status_code, immediate=True)
        return None
    if r.status_code >= 500:
        ctx.on_failure(status=r.status_code)
        return None
    ctx.on_success()
    try:
        tree = html.fromstring(r.text)
        nodes = tree.xpath('//meta[@name="_csrf"]/@content')
    except Exception:
        return None
    if not nodes:
        return None
    return ({"User-Agent": _UA, "X-CSRF-Token": nodes[0], "Referer": page_url},
            dict(r.cookies))


def _api_get(ctx: FetchContext, url: str, params: dict, headers: dict,
             cookies: dict, timeout: int = 15) -> Any:
    """带 CSRF 头 + cookie 的 API 请求（限流 + 熔断反馈）。失败返回 None。"""
    if not ctx.acquire():
        return None
    try:
        r = egress_request("get", ctx.source.egress_client, url, params=params,
                           headers=headers, cookies=cookies, timeout=timeout)
    except Exception:
        ctx.on_network_error()
        return None
    if r.status_code in (403, 429):
        ctx.on_failure(status=r.status_code, immediate=True)
        return None
    if r.status_code >= 500:
        ctx.on_failure(status=r.status_code)
        return None
    ctx.on_success()
    try:
        return r.json()
    except ValueError:
        return None


@capability(
    name="legulegu",
    domain="估值历史",
    sources=[SourceMeta(name="legulegu", group="legulegu")],
    default_source="legulegu",
    data_type="table",  # 历史时间序列
    cache_policy="daily_settled",  # 历史数据日级慢变（L 档），盘后定稿
    supported_formats=["json", "csv", "md", "xlsx"],
)
def fetch_legulegu(ctx: FetchContext, lg_type: str, market: str = "上证",
                   **_unused) -> list[dict] | None:
    """乐咕全市场 PE/PB 历史。lg_type ∈ {pe, pb}。

    pe: market ∈ {上证, 深证, 创业板}，返回 [{date, close, pe}, ...]
    pb: market ∈ {上证, 深证, 创业板, 科创版}，返回 [{date, close, pb, add_pb}, ...]
    """
    if lg_type == "pe":
        return _fetch_pe(ctx, market)
    if lg_type == "pb":
        return _fetch_pb(ctx, market)
    return []


def _fetch_pe(ctx: FetchContext, market: str) -> list[dict] | None:
    if market not in _PE_MARKET:
        return []  # 服务端不报错（科创版走单独 URL，暂不支持）
    csrf = _get_csrf(ctx, _PE_PAGE[market])
    if csrf is None:
        return None
    headers, cookies = csrf
    d = _api_get(ctx, "https://legulegu.com/api/stock-data/market-pe",
                 params={"token": _legu_token(), "marketId": _PE_MARKET[market]},
                 headers=headers, cookies=cookies)
    if d is None:
        return None
    data = d.get("data", [])
    return [{"date": str(row.get("date", ""))[:10],
             "close": row.get("close"), "pe": row.get("pe")} for row in data]


def _fetch_pb(ctx: FetchContext, market: str) -> list[dict] | None:
    if market not in _PB_MARKET:
        return []
    csrf = _get_csrf(ctx, _PB_PAGE[market])
    if csrf is None:
        return None
    headers, cookies = csrf
    d = _api_get(ctx, "https://legulegu.com/api/stockdata/index-basic-pb",
                 params={"token": _legu_token(), "indexCode": _PB_MARKET[market]},
                 headers=headers, cookies=cookies)
    if d is None:
        return None
    data = d.get("data", [])
    return [{"date": str(row.get("date", ""))[:10],
             "close": row.get("close"), "pb": row.get("pb"),
             "add_pb": row.get("addPb")} for row in data]
