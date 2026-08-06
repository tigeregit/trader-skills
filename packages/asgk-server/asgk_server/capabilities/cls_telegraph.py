"""cls_telegraph 能力 — 财联社电报（全市场实时快讯）。

把 asgk/news.py.cls_telegraph 的上游知识下沉到服务端：
  - URL: https://www.cls.cn/v1/roll/get_roll_list
  - **签名算法**：md5(sha1(按 key 字典序拼接的 query 串))，纯本地算、无需 key
    （query 串 = "&".join(f"{k}={params[k]}" for k in sorted(params))）
  - params（appName/os/sv/last_time/refresh_type/rn）
  - ctime 是 Unix 秒，转 YYYY-MM-DD HH:MM:SS

签名算法下沉是关键——客户端零签名知识，只发语义请求「要 N 条电报」。
这是能力代理相对透明代理的核心收益：签名/鉴权细节由服务端单点持有。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

_URL = "https://www.cls.cn/v1/roll/get_roll_list"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


@capability(
    name="cls_telegraph",
    domain="新闻",
    sources=[SourceMeta(name="cls", group="cls")],
    default_source="cls",
    data_type="table",
    cache_policy="streaming",  # 电报流式追加，no-cache + singleflight
    supported_formats=["json", "csv", "md", "xlsx"],
)
def fetch_cls_telegraph(ctx: FetchContext, page_size: int = 50,
                        **_unused) -> list[dict] | None:
    """财联社电报（全市场实时快讯）。

    Returns:
        [{title, content, time(YYYY-MM-DD HH:MM:SS)}, ...]
    """
    params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5",
              "last_time": "", "refresh_type": "1", "rn": str(page_size)}
    # 签名：md5(sha1(按 key 字典序拼接的 query 串))，纯本地算、无需 key
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sign = hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()
    url = f"{_URL}?{qs}&sign={sign}"
    if not ctx.acquire():
        return None
    try:
        r = egress_request("get", ctx.source.egress_client, url,
                           headers={"User-Agent": _UA, "Referer": "https://www.cls.cn/"},
                           timeout=10)
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
    rows: list[dict[str, Any]] = []
    for item in r.json().get("data", {}).get("roll_data", []) or []:
        ts = item.get("ctime")
        t = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        rows.append({
            "title": item.get("title", "") or item.get("brief", ""),
            "content": item.get("content", "") or item.get("brief", ""),
            "time": t,
        })
    return rows
