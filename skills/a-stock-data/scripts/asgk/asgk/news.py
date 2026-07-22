"""asgk.news — 新闻层（个股新闻/财联社电报/全球资讯）。

移植自 ref/a-stock-data SKILL.md §5.1-5.3。按 asgk-contract.md 契约：
  - 5.1 东财个股新闻：经网关(search-api-web)，JSONP 解析，tier=N
  - 5.2 财联社电报：直连(cls.cn，本地签名零key)，tier=N
  - 5.3 东财全球资讯：经网关(np-weblist)，strip=req_trace，tier=N
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime

import requests

from asgk._contract import source
from asgk.em_proxy import em_get

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


@source(tier="N", via="gateway")
def eastmoney_stock_news(code: str, page_size: int = 20) -> list[dict]:
    """东财个股新闻（JSONP 接口）。

    Args:
        code: 6位股票代码
        page_size: 条数
    Returns:
        [{title, content(纯文本截断200字), time, source, url}, ...]
    Note:
        部分大陆住宅 IP 间歇只返回 passportWeb 无文章列表（东财风控），空时返回 []。
    """
    inner_params = json.dumps({
        "uid": "", "keyword": code, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                  "pageIndex": 1, "pageSize": page_size, "preTag": "", "postTag": ""}},
    }, separators=(",", ":"))
    r = em_get("https://search-api-web.eastmoney.com/search/jsonp",
               params={"cb": "jQuery_news", "param": inner_params},
               headers={"Referer": "https://so.eastmoney.com/"}, timeout=15, tier="N")
    # 解析 JSONP：去掉 cb(...) 包裹
    text = r.text
    json_str = text[text.index("(") + 1:text.rindex(")")]
    d = json.loads(json_str)
    articles = d.get("result", {}).get("cmsArticleWebOld", []) or []
    return [{
        "title": re.sub(r'<[^>]+>', '', a.get("title", "")),
        "content": re.sub(r'<[^>]+>', '', a.get("content", ""))[:200],
        "time": a.get("date", ""), "source": a.get("mediaName", ""), "url": a.get("url", ""),
    } for a in articles]


@source(tier="N", via="direct")
def cls_telegraph(page_size: int = 50) -> list[dict]:
    """财联社电报（全市场实时快讯）。v1 API + 本地签名，零 key。

    Returns:
        [{title, content, time(YYYY-MM-DD HH:MM:SS)}, ...]
    """
    params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5",
              "last_time": "", "refresh_type": "1", "rn": str(page_size)}
    # 签名：md5(sha1(按 key 字典序拼接的 query 串))，纯本地算、无需 key
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sign = hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()
    url = f"https://www.cls.cn/v1/roll/get_roll_list?{qs}&sign={sign}"
    r = requests.get(url, headers={"User-Agent": UA, "Referer": "https://www.cls.cn/"}, timeout=10)
    rows = []
    for item in r.json().get("data", {}).get("roll_data", []) or []:
        ts = item.get("ctime")
        t = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        rows.append({
            "title": item.get("title", "") or item.get("brief", ""),
            "content": item.get("content", "") or item.get("brief", ""),
            "time": t,
        })
    return rows


@source(tier="N", via="gateway", strip=["req_trace"])
def eastmoney_global_news(page_size: int = 50) -> list[dict]:
    """东方财富全球财经资讯（7×24 滚动）。

    Returns:
        [{title, summary(截断200字), time}, ...]
    Note:
        @source(strip=["req_trace"])：req_trace 每次是 uuid4，指纹哈希时需剔除
        （否则每次响应哈希都不同，误判为高频变化）。见 gateway-design §3.4.7。
    """
    from datetime import datetime
    # sortEnd 必须传日期（空串会报 Required parameter 错误），默认当天取最新
    today = datetime.now().strftime("%Y-%m-%d")
    r = em_get("https://np-weblist.eastmoney.com/comm/web/getFastNewsList",
               params={"client": "web", "biz": "web_724", "fastColumn": "102",
                       "sortEnd": today, "pageSize": str(page_size),
                       "req_trace": str(uuid.uuid4())},
               headers={"Referer": "https://kuaixun.eastmoney.com/"}, timeout=10, tier="N")
    data = r.json().get("data") or {}
    return [{
        "title": item.get("title", ""),
        "summary": item.get("summary", "")[:200],
        "time": item.get("showTime", ""),
    } for item in data.get("fastNewsList", [])]
