"""news 能力 — 东财新闻（个股 JSONP + 全球资讯）。

把 asgk/news.py.eastmoney_stock_news（JSONP 剥壳）与 eastmoney_global_news 下沉。
两函数走不同端点，用 news_type 参数区分（stock / global）。

JSONP 剥壳（cb({...}) → JSON）、HTML 标签清洗、req_trace 剔除全部下沉服务端。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

_SEARCH_API = "https://search-api-web.eastmoney.com/search/jsonp"
_GLOBAL_API = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


def _get(ctx: FetchContext, url: str, params: dict, headers: dict,
         timeout: int = 15) -> str | None:
    """通用 GET 取文本（news 需原始 text 做 JSONP 剥壳）。失败返回 None。"""
    h = {"User-Agent": _UA}
    h.update(headers)
    if not ctx.acquire():
        return None
    try:
        r = egress_request("get", ctx.source.egress_client, url,
                           params=params, headers=h, timeout=timeout)
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
    return r.text


def _strip_jsonp(text: str) -> Any:
    """JSONP 剥壳：cb({...}) → dict；纯 JSON 兜底；失败返回 None。"""
    text = text.strip()
    if "(" in text and ")" in text[text.index("(") + 1:]:
        json_str = text[text.index("(") + 1:text.rindex(")")]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


@capability(
    name="news",
    domain="新闻",
    sources=[SourceMeta(name="eastmoney", group="eastmoney")],
    default_source="eastmoney",
    data_type="table",
    cache_policy="streaming",  # 新闻流式追加，no-cache + singleflight
    supported_formats=["json", "csv", "md", "xlsx"],
)
def fetch_news(ctx: FetchContext, news_type: str, code: str = "",
               page_size: int = 20, **_unused) -> list[dict]:
    """东财新闻。news_type ∈ {stock, global}。

    stock:  个股新闻（search-api JSONP），code=6位代码
    global: 全球财经资讯（np-weblist 7×24 滚动）
    """
    if news_type == "stock":
        inner = json.dumps({
            "uid": "", "keyword": code, "type": ["cmsArticleWebOld"],
            "client": "web", "clientType": "web", "clientVersion": "curr",
            "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                      "pageIndex": 1, "pageSize": page_size, "preTag": "", "postTag": ""}},
        }, separators=(",", ":"))
        text = _get(ctx, _SEARCH_API,
                    params={"cb": "jQuery_news", "param": inner},
                    headers={"Referer": "https://so.eastmoney.com/"})
        if text is None:
            return None  # type: ignore[return-value]
        d = _strip_jsonp(text)
        if not isinstance(d, dict):
            return []
        articles = (d.get("result") or {}).get("cmsArticleWebOld", []) or []
        return [{
            "title": re.sub(r'<[^>]+>', '', a.get("title", "")),
            "content": re.sub(r'<[^>]+>', '', a.get("content", ""))[:200],
            "time": a.get("date", ""), "source": a.get("mediaName", ""),
            "url": a.get("url", ""),
        } for a in articles]

    if news_type == "global":
        today = datetime.now().strftime("%Y-%m-%d")
        text = _get(ctx, _GLOBAL_API,
                    params={"client": "web", "biz": "web_724", "fastColumn": "102",
                            "sortEnd": today, "pageSize": str(page_size),
                            "req_trace": str(uuid4())},
                    headers={"Referer": "https://kuaixun.eastmoney.com/"})
        if text is None:
            return None  # type: ignore[return-value]
        try:
            data = json.loads(text).get("data") or {}
        except (json.JSONDecodeError, AttributeError):
            return []
        return [{
            "title": item.get("title", ""),
            "summary": item.get("summary", "")[:200],
            "time": item.get("showTime", ""),
        } for item in data.get("fastNewsList", [])]

    return []
