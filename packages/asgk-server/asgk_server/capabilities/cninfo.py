"""cninfo 能力 — 巨潮公告检索 + 互动易问答。

把 asgk/announce.py.cninfo_announcements 与 asgk/sentiment.py.cninfo_irm 的上游
知识下沉到服务端。两函数都走 cninfo 源（POST form），用 cninfo_type 参数区分。

**cninfo_announcements（announce）**：
  - orgId 动态映射（关键）：巨潮 orgId 非统一 gssx0{code} 格式（如 601318→
    9900002221），硬编码会导致大量股票返回 totalAnnouncement=0。服务端持有模块级
    orgId 映射缓存（首次拉取 szse_stock.json 全程复用，所有 agent 共享），未命中
    回退硬编码规则。
  - POST form-encoded 到 hisAnnouncement/query。
  - announcementTime 是 Unix 毫秒，转 YYYY-MM-DD。

**cninfo_irm（irm）**：两步 POST 流：
  - ① POST queryKeyboardInfo（form body {keyWord: code}）拿 orgId（secid）
  - ② POST question（参数放 query string，空 body）拿问答
  - pubDate 是 Unix 毫秒，转 YYYY-MM-DD HH:MM。

服务端闭环两步流（解决无状态代理无法保持会话的问题）——这正是能力代理相对透明
代理的核心收益之一。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"
_ORGID_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
_ANNO_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_IRM_KEYINFO = "https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo"
_IRM_QUESTION = "https://irm.cninfo.com.cn/newircs/company/question"

# 模块级 orgId 映射缓存（首次拉取全程复用，所有 agent 共享——服务端单例）
_ORGID_MAP: dict[str, str] = {}


def _cninfo_ts_to_date(ts: Any) -> str:
    """巨潮 announcementTime 是 Unix 毫秒，转 YYYY-MM-DD。"""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
    return str(ts)[:10] if ts else ""


def _get_orgid(ctx: FetchContext, code: str) -> str:
    """查股票真实 orgId。

    优先用模块级缓存（首次拉取 szse_stock.json 全程复用）；未命中回退硬编码规则
    （gssh0{code} / gsbj0{code} / gssz0{code}）。

    orgId 映射拉取走限流 + 熔断反馈（与公告请求共享 cninfo 组配额）。
    """
    global _ORGID_MAP
    if not _ORGID_MAP:
        if ctx.acquire():
            try:
                r = egress_request("get", ctx.source.egress_client, _ORGID_URL,
                                   headers={"User-Agent": _UA}, timeout=15)
                if r.status_code in (403, 429):
                    ctx.on_failure(status=r.status_code, immediate=True)
                elif r.status_code >= 500:
                    ctx.on_failure(status=r.status_code)
                else:
                    ctx.on_success()
                    _ORGID_MAP = {s["code"]: s["orgId"]
                                  for s in r.json().get("stockList", [])}
            except Exception:
                ctx.on_network_error()
        # 拉取失败也继续（回退硬编码）
    org = _ORGID_MAP.get(code)
    if org:
        return org
    if code.startswith("6"):
        return f"gssh0{code}"
    if code.startswith(("8", "4")):
        return f"gsbj0{code}"
    return f"gssz0{code}"


def _post(ctx: FetchContext, url: str, data: dict | None = None,
          params: dict | None = None, headers: dict | None = None,
          timeout: int = 15) -> Any:
    """通用 cninfo POST：限流→出网→熔断反馈→返回 r.json()。失败返回 None。"""
    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    if not ctx.acquire():
        return None
    try:
        r = egress_request("post", ctx.source.egress_client, url, data=data,
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
    try:
        return r.json()
    except ValueError:
        return None


@capability(
    name="cninfo",
    domain="公告",
    sources=[SourceMeta(name="cninfo", group="cninfo")],
    default_source="cninfo",
    data_type="table",
    cache_policy="definitive",  # 公告/问答发布即定稿，永不变（P 档）
    supported_formats=["json", "csv", "md", "xlsx"],
)
def fetch_cninfo(ctx: FetchContext, cninfo_type: str, code: str = "",
                 page_size: int = 30, page_num: int = 1, **_unused) -> list[dict] | None:
    """巨潮公告 / 互动易问答。cninfo_type ∈ {announce, irm}。

    announce: 公告全文检索（需 code），orgId 动态映射 + POST form
    irm:      互动易问答（需 code），两步 POST 流（queryKeyboardInfo → question）
    """
    if cninfo_type == "announce":
        return _fetch_announce(ctx, code, page_size)
    if cninfo_type == "irm":
        return _fetch_irm(ctx, code, page_size, page_num)
    return []


def _fetch_announce(ctx: FetchContext, code: str, page_size: int) -> list[dict]:
    """公告检索：orgId 解析 → POST hisAnnouncement/query。"""
    org_id = _get_orgid(ctx, code)
    payload = {
        "stock": f"{code},{org_id}", "tabName": "fulltext",
        "pageSize": str(page_size), "pageNum": "1",
        "column": "", "category": "", "plate": "", "seDate": "",
        "searchkey": "", "secid": "", "sortName": "", "sortType": "",
        "isHLtitle": "true",
    }
    headers = {"Referer": "https://www.cninfo.com.cn/new/disclosure",
               "Origin": "https://www.cninfo.com.cn"}
    d = _post(ctx, _ANNO_URL, data=payload, headers=headers, timeout=15)
    if d is None:
        return None  # type: ignore[return-value]
    return [{
        "title": item.get("announcementTitle", ""),
        "type": item.get("announcementTypeName", ""),
        "date": _cninfo_ts_to_date(item.get("announcementTime")),
        "url": f"https://www.cninfo.com.cn/new/disclosure/detail?annoId={item.get('announcementId', '')}",
    } for item in d.get("announcements", []) or []]


def _fetch_irm(ctx: FetchContext, code: str, page_size: int, page_num: int) -> list[dict]:
    """互动易：两步 POST（queryKeyboardInfo 拿 orgId → question 拿问答）。"""
    d1 = _post(ctx, _IRM_KEYINFO, data={"keyWord": code}, timeout=10)
    if d1 is None:
        return None  # type: ignore[return-value]
    data1 = d1.get("data") or []
    if not data1:
        return []
    org_id = data1[0].get("secid")
    params = {"_t": "1", "stockcode": code, "orgId": org_id, "pageSize": str(page_size),
              "pageNum": str(page_num), "keyWord": "", "startDay": "", "endDay": ""}
    d2 = _post(ctx, _IRM_QUESTION, params=params, timeout=10)
    if d2 is None:
        return None  # type: ignore[return-value]
    rows = d2.get("rows") or []
    out = []
    for it in rows:
        pd = it.get("pubDate")
        out.append({"code": it.get("stockCode"), "company": it.get("companyShortName"),
            "question": it.get("mainContent"), "answer": it.get("attachedContent"),
            "answerer": it.get("attachedAuthor"),
            "ask_time": datetime.fromtimestamp(pd / 1000).strftime("%Y-%m-%d %H:%M") if pd else ""})
    return out
