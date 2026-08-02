"""asgk.announce — 公告层（巨潮公告检索）。

实现约定：
  - 巨潮 cninfo.com.cn 直连（POST，不在风控组），tier=P（发布即定稿）
  - 含 orgId 动态映射，避免硬编码导致部分股票代码查不到公告
"""
from __future__ import annotations

from datetime import datetime

import requests

from asgk._contract import source

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"

# 巨潮 股票→orgId 映射（模块级缓存，首次拉取全程复用）
_CNINFO_ORGID_MAP: dict[str, str] = {}


def _cninfo_ts_to_date(ts) -> str:
    """巨潮 announcementTime 是 Unix 毫秒，转 YYYY-MM-DD。"""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
    return str(ts)[:10] if ts else ""


def _cninfo_orgid(code: str) -> str:
    """查股票真实 orgId。

    巨潮 orgId 非统一 gssx0{code} 格式（如 601318→9900002221），硬编码会导致
    大量股票（尤其 601xxx 段）返回 totalAnnouncement=0（#19）。优先动态查官方映射表。
    """
    global _CNINFO_ORGID_MAP
    if not _CNINFO_ORGID_MAP:
        try:
            r = requests.get("http://www.cninfo.com.cn/new/data/szse_stock.json",
                             headers={"User-Agent": UA}, timeout=15)
            _CNINFO_ORGID_MAP = {s["code"]: s["orgId"]
                                 for s in r.json().get("stockList", [])}
        except Exception:
            pass  # 回退硬编码
    org = _CNINFO_ORGID_MAP.get(code)
    if org:
        return org
    if code.startswith("6"):
        return f"gssh0{code}"
    if code.startswith(("8", "4")):
        return f"gsbj0{code}"
    return f"gssz0{code}"


@source(tier="P", via="direct", cli="announce")
def cninfo_announcements(code: str, page_size: int = 30) -> list[dict]:
    """巨潮公告全文检索。

    Args:
        code: 6位股票代码
        page_size: 条数
    Returns:
        [{title, type, date, url}, ...]
    """
    org_id = _cninfo_orgid(code)
    payload = {
        "stock": f"{code},{org_id}", "tabName": "fulltext",
        "pageSize": str(page_size), "pageNum": "1",
        "column": "", "category": "", "plate": "", "seDate": "",
        "searchkey": "", "secid": "", "sortName": "", "sortType": "",
        "isHLtitle": "true",
    }
    headers = {"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded",
               "Referer": "https://www.cninfo.com.cn/new/disclosure",
               "Origin": "https://www.cninfo.com.cn"}
    r = requests.post("https://www.cninfo.com.cn/new/hisAnnouncement/query",
                      data=payload, headers=headers, timeout=15)
    return [{
        "title": item.get("announcementTitle", ""),
        "type": item.get("announcementTypeName", ""),
        "date": _cninfo_ts_to_date(item.get("announcementTime")),
        "url": f"https://www.cninfo.com.cn/new/disclosure/detail?annoId={item.get('announcementId', '')}",
    } for item in r.json().get("announcements", []) or []]
