"""ths_signal 能力 — 同花顺信号（热点/北向/热榜）。

把 asgk/signal.py 的 ths_hot_reason / hsgt_realtime 与 asgk/sentiment.py 的
ths_hot_list 下沉。三函数走同花顺系（zx.10jqka / data.hexin / dq.10jqka），
用 signal_type 参数区分（hot_reason/hsgt/hot_list）。各自解析逻辑下沉。
"""
from __future__ import annotations

from typing import Any

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


def _get(ctx: FetchContext, url: str, params: dict | None = None,
         headers: dict | None = None, timeout: int = 10) -> Any:
    """同花顺系通用 GET：限流→出网→熔断反馈→r.json()。失败返回 None。"""
    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    if not ctx.acquire():
        return None
    try:
        r = egress_request("get", ctx.source.egress_client, url,
                           params=params or {}, headers=h, timeout=timeout)
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
    name="ths_signal",
    domain="信号",
    sources=[SourceMeta(name="10jqka", group="10jqka")],
    default_source="10jqka",
    data_type="table",
    cache_policy="daily_volatile",  # 热点/热榜日内变（1h TTL）；hsgt 实时但合并到此能力
    supported_formats=["json", "csv", "md", "xlsx"],
)
def fetch_ths_signal(ctx: FetchContext, signal_type: str,
                     date: str | None = None, period: str = "hour",
                     **_unused) -> Any:
    """同花顺信号。signal_type ∈ {hot_reason, hsgt, hot_list}。

    hot_reason: 当日强势股 + 题材归因（zx.10jqka，date=YYYY-MM-DD）
    hsgt:       沪深股通实时分钟流向（data.hexin）
    hot_list:   同花顺热榜（dq.10jqka，period=hour/day）
    """
    if signal_type == "hot_reason":
        url = (f"http://zx.10jqka.com.cn/event/api/getharden/date/{date}/"
               f"orderby/date/orderway/desc/charset/GBK/")
        data = _get(ctx, url)
        if data is None:
            return None  # type: ignore[return-value]
        if data.get("errocode", 0) != 0:
            return []  # 同花顺错误 → 空（不抛，客户端拿 [] 处理）
        return data.get("data") or []

    if signal_type == "hsgt":
        data = _get(ctx, "https://data.hexin.cn/market/hsgtApi/method/dayChart/",
                    headers={"Referer": "https://data.hexin.cn/"})
        if data is None:
            return None  # type: ignore[return-value]
        times = data.get("time", [])
        hgt = data.get("hgt", [])
        sgt = data.get("sgt", [])
        n = len(times)
        return [{"time": times[i],
                 "hgt_yi": hgt[i] if i < len(hgt) else None,
                 "sgt_yi": sgt[i] if i < len(sgt) else None}
                for i in range(n)]

    if signal_type == "hot_list":
        data = _get(ctx, "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock",
                    params={"stock_type": "a", "type": period, "list_type": "normal"})
        if data is None:
            return None  # type: ignore[return-value]
        lst = (data.get("data") or {}).get("stock_list") or []
        out = []
        for it in lst:
            tag = it.get("tag") or {}
            out.append({
                "rank": it.get("order"), "code": it.get("code"), "name": it.get("name"),
                "heat": it.get("rate"), "pct": it.get("rise_and_fall"),
                "rank_chg": it.get("hot_rank_chg"),
                "concepts": tag.get("concept_tag") or [],
                "tag": tag.get("popularity_tag", "")})
        return out

    return []  # 未知 signal_type → 空
