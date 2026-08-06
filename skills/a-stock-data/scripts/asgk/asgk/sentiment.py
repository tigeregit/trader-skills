"""asgk.sentiment — 舆情互动层（互动易/热榜/人气榜/概念命中）。

实现约定：
  - 互动易 irm.cninfo.com.cn 经网关（cninfo 组，POST form），tier=P（发布即定稿）
  - 同花顺热榜 dq.10jqka 经网关，tier=R
  - 东财人气榜/概念 emappdata 经网关，tier=R/S
"""
from __future__ import annotations

from datetime import datetime

from asgk._contract import source
from asgk.em_proxy import _server_call, em_get

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"
EM_HOT_BODY = {"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38"}


@source(tier="P", via="gateway", data_type="table")
def cninfo_irm(code: str, page_size: int = 30, page_num: int = 1) -> list[dict]:
    """互动易问答（深沪统一走巨潮）。

    Args:
        code: 6位代码
    Returns:
        每条含 code/company/question(提问)/answer(回复,None=未回复)/answerer/ask_time。
    Note:
        两步请求：① queryKeyboardInfo 拿 orgId（form body）；② question 拿问答
        （参数放 query string，空 body）。

    取数路径（§3.4）：优先调 cninfo 能力（cninfo_type=irm，两步 POST 流在服务端
    闭环——解决无状态代理无法保持会话的问题），回退旧路径。
    """
    data = _server_call("cninfo", {"cninfo_type": "irm", "code": code,
                                   "page_size": page_size, "page_num": page_num})
    if data is not None:
        return data
    return _cninfo_irm_legacy(code, page_size, page_num)


def _cninfo_irm_legacy(code: str, page_size: int = 30, page_num: int = 1) -> list[dict]:
    """回退路径：经 sgw 网关两步 POST，本地解析。"""
    try:
        r1 = em_get("https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo",
                    data={"keyWord": code}, headers={"User-Agent": UA},
                    timeout=10, tier="P", method="POST")
        d1 = r1.json().get("data") or []
        if not d1:
            return []
        org_id = d1[0].get("secid")
        params = {"_t": "1", "stockcode": code, "orgId": org_id, "pageSize": str(page_size),
                  "pageNum": str(page_num), "keyWord": "", "startDay": "", "endDay": ""}
        r2 = em_get("https://irm.cninfo.com.cn/newircs/company/question",
                    params=params, headers={"User-Agent": UA}, timeout=10, tier="P", method="POST")
        rows = r2.json().get("rows") or []
    except Exception:
        return []
    out = []
    for it in rows:
        pd = it.get("pubDate")
        out.append({"code": it.get("stockCode"), "company": it.get("companyShortName"),
            "question": it.get("mainContent"), "answer": it.get("attachedContent"),
            "answerer": it.get("attachedAuthor"),
            "ask_time": datetime.fromtimestamp(pd / 1000).strftime("%Y-%m-%d %H:%M") if pd else ""})
    return out


@source(tier="R", via="gateway", data_type="table")
def ths_hot_list(period: str = "hour") -> list[dict]:
    """同花顺热榜（名称+人气+概念标签+排名变化）。

    Args:
        period: "hour" / "day"
    Returns:
        每只含 rank/code/name/heat(人气)/pct/rank_chg/concepts(标签)/tag。

    取数路径（§3.4）：优先调 ths_signal 能力（signal_type=hot_list），回退旧路径。
    """
    data = _server_call("ths_signal", {"signal_type": "hot_list", "period": period})
    if data is not None:
        return data
    return _ths_hot_list_legacy(period)


def _ths_hot_list_legacy(period: str = "hour") -> list[dict]:
    """回退路径：经 sgw 网关取 dq.10jqka hot_list，本地解析。"""
    r = em_get("https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock",
               params={"stock_type": "a", "type": period, "list_type": "normal"},
               headers={"User-Agent": UA}, timeout=10, tier="R")
    lst = (r.json().get("data") or {}).get("stock_list") or []
    out = []
    for it in lst:
        tag = it.get("tag") or {}
        out.append({"rank": it.get("order"), "code": it.get("code"), "name": it.get("name"),
            "heat": it.get("rate"), "pct": it.get("rise_and_fall"), "rank_chg": it.get("hot_rank_chg"),
            "concepts": tag.get("concept_tag") or [], "tag": tag.get("popularity_tag", "")})
    return out


@source(tier="R", via="gateway", data_type="table")
def em_hot_rank(top: int = 50) -> list[dict]:
    """东财人气榜（排名 + 排名变化 + 名称/价格）。

    Returns: 每只含 rank/code/name/price/pct/rank_chg。名称需二次请求 push2 ulist 补全。
    Note: 人气榜 POST+JSON 经网关（body 进 cache key）；补全名称的 ulist GET 经网关。

    取数路径（§3.4）：优先调 em_hot 能力（hot_type=rank），回退旧路径。
    """
    data = _server_call("em_hot", {"hot_type": "rank", "top": top})
    if data is not None:
        return data
    return _em_hot_rank_legacy(top)


def _em_hot_rank_legacy(top: int = 50) -> list[dict]:
    """回退路径：经 sgw 网关取 emappdata POST + push2 ulist 补名。"""
    r = em_get("https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
               json={**EM_HOT_BODY, "marketType": "", "pageNo": 1, "pageSize": top},
               headers={"User-Agent": UA}, timeout=10, tier="R", method="POST")
    data = r.json().get("data") or []
    if not data:
        return []
    # 用 push2 ulist 批量补名称/价格
    secids = [("0." if it["sc"].startswith("SZ") else "1.") + it["sc"][2:] for it in data]
    u = em_get("https://push2.eastmoney.com/api/qt/ulist.np/get",
               params={"ut": "f057cbcbce2a86e2866ab8877db1d059", "fltt": 2, "invt": 2,
                       "fields": "f14,f3,f12,f2", "secids": ",".join(secids)},
               headers={"Referer": "https://quote.eastmoney.com/"}, timeout=10, tier="R")
    diff = (u.json().get("data") or {}).get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    nm = {x["f12"]: (x.get("f14"), x.get("f2"), x.get("f3")) for x in diff}
    return [{"rank": it["rk"], "code": it["sc"][2:],
             "name": nm.get(it["sc"][2:], ("",))[0],
             "price": nm.get(it["sc"][2:], (None, None))[1],
             "pct": nm.get(it["sc"][2:], (None, None, None))[2],
             "rank_chg": it.get("hisRc")} for it in data]


@source(tier="S", via="gateway", data_type="table")
def em_hot_concept(code: str) -> list[dict]:
    """东财个股热门概念命中（这只票当下被市场归到哪些概念在炒）。

    Returns: [{concept, bk, hit(命中热度)}, ...] 按热度降序。
    Note: POST+JSON 经网关（body 含股票代码，进 cache key）。

    取数路径（§3.4）：优先调 em_hot 能力（hot_type=concept），回退旧路径。
    """
    data = _server_call("em_hot", {"hot_type": "concept", "code": code})
    if data is not None:
        return data
    return _em_hot_concept_legacy(code)


def _em_hot_concept_legacy(code: str) -> list[dict]:
    """回退路径：经 sgw 网关取 emappdata POST，本地解析。"""
    prefix = "SH" if code.startswith("6") else "SZ"
    r = em_get("https://emappdata.eastmoney.com/stockrank/getHotStockRankList",
               json={**EM_HOT_BODY, "srcSecurityCode": prefix + code},
               headers={"User-Agent": UA}, timeout=10, tier="S", method="POST")
    data = r.json().get("data") or []
    return [{"concept": x.get("conceptName"), "bk": x.get("conceptId"),
             "hit": x.get("hitCount")} for x in data]
