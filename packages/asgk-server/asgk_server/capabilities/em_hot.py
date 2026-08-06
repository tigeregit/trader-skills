"""em_hot 能力 — 东财人气榜/个股热门概念（emappdata POST + push2 ulist 补名）。

把 asgk/sentiment.py 的 em_hot_rank / em_hot_concept 下沉。两函数共用 emappdata
POST+JSON（EM_HOT_BODY 固定 appId/globalId），用 hot_type 参数区分（rank/concept）。
hot_rank 还需 push2 ulist 二次补名（sc→f12 匹配取 name/price）。
"""
from __future__ import annotations

from typing import Any

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

_EMAPPDATA = "https://emappdata.eastmoney.com/stockrank"
_PUSH2_ULIST = "https://push2.eastmoney.com/api/qt/ulist.np/get"
_EM_HOT_BODY = {"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38"}
_ULIST_UT = "f057cbcbce2a86e2866ab8877db1d059"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


def _post_json(ctx: FetchContext, url: str, body: dict,
               headers: dict | None = None, timeout: int = 10) -> Any:
    """emappdata POST+JSON：限流→出网→熔断反馈→r.json()。失败返回 None。"""
    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    if not ctx.acquire():
        return None
    try:
        r = egress_request("post", ctx.source.egress_client, url,
                           json=body, headers=h, timeout=timeout)
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
    name="em_hot",
    domain="舆情",
    sources=[SourceMeta(name="eastmoney", group="eastmoney")],
    default_source="eastmoney",
    data_type="table",
    cache_policy="daily_volatile",  # 人气榜/概念日内变，1h TTL
    supported_formats=["json", "csv", "md", "xlsx"],
)
def fetch_em_hot(ctx: FetchContext, hot_type: str, top: int = 50,
                 code: str = "", **_unused) -> list[dict]:
    """东财人气榜/个股热门概念。hot_type ∈ {rank, concept}。

    rank:    人气榜（getAllCurrentList POST），含 push2 ulist 二次补名
    concept: 个股热门概念（getHotStockRankList POST，code 指定股票）
    """
    if hot_type == "rank":
        data = _post_json(ctx, f"{_EMAPPDATA}/getAllCurrentList",
                          body={**_EM_HOT_BODY, "marketType": "",
                                "pageNo": 1, "pageSize": top})
        if data is None:
            return None  # type: ignore[return-value]
        rank_data = data.get("data") or []
        if not rank_data:
            return []
        # push2 ulist GET 补名（二次请求）
        secids = [("0." if it["sc"].startswith("SZ") else "1.") + it["sc"][2:]
                  for it in rank_data]
        if not ctx.acquire():
            return None  # type: ignore[return-value]
        try:
            ur = egress_request("get", ctx.source.egress_client, _PUSH2_ULIST,
                                params={"ut": _ULIST_UT, "fltt": 2, "invt": 2,
                                        "fields": "f14,f3,f12,f2",
                                        "secids": ",".join(secids)},
                                headers={"Referer": "https://quote.eastmoney.com/"},
                                timeout=10)
        except Exception:
            ctx.on_network_error()
            return None  # type: ignore[return-value]
        if ur.status_code in (403, 429):
            ctx.on_failure(status=ur.status_code, immediate=True)
            return None  # type: ignore[return-value]
        if ur.status_code >= 500:
            ctx.on_failure(status=ur.status_code)
            return None  # type: ignore[return-value]
        ctx.on_success()
        udata = ur.json()
        diff = (udata.get("data") or {}).get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        nm = {x["f12"]: (x.get("f14"), x.get("f2"), x.get("f3")) for x in diff}
        return [{"rank": it["rk"], "code": it["sc"][2:],
                 "name": nm.get(it["sc"][2:], ("",))[0],
                 "price": nm.get(it["sc"][2:], (None, None))[1],
                 "pct": nm.get(it["sc"][2:], (None, None, None))[2],
                 "rank_chg": it.get("hisRc")} for it in rank_data]

    if hot_type == "concept":
        prefix = "SH" if code.startswith("6") else "SZ"
        data = _post_json(ctx, f"{_EMAPPDATA}/getHotStockRankList",
                          body={**_EM_HOT_BODY, "srcSecurityCode": prefix + code})
        if data is None:
            return None  # type: ignore[return-value]
        cdata = data.get("data") or []
        return [{"concept": x.get("conceptName"), "bk": x.get("conceptId"),
                 "hit": x.get("hitCount")} for x in cdata]

    return []
