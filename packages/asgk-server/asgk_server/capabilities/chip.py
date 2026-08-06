"""chip 能力 — 筹码分布 + 主力成本（cyq.js 本地计算）。

把 asgk/chip.py.chip_distribution 的上游知识 + 算法下沉到服务端：
  - K 线获取：push2his（eastmoney 组，secid 市场前缀 + klt=101 日K + fqt 复权）
  - 百度降级链：push2his 在部分网络/时段返回 rc=0,data=null；用本服务端
    baidu_kline capability 继续取日 K（避免上游空响应变成永久空结果）
  - CYQ 计算：py_mini_racer 执行 vendor cyq.js（CYQCalculator，纯数学无 DOM）
  - 返回最近 90 日筹码分布

cyq.js 已复制到 asgk_server/resources/cyq.js（服务端自包含，不依赖客户端 vendor）。

客户端发 {code, adjust}，服务端取 K 线 + 跑 CYQ + 返回最近 90 日结构化数据。

注：MiniRacer context 的线程安全需并发测试验证；此处用线程锁保护 js.call，
避免并发调用 CYQCalculator 时引擎状态串读（py_mini_racer 官方不保证线程安全）。
"""
from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"
_REFERER = "https://quote.eastmoney.com/"
_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

_CYQ_JS_PATH = Path(__file__).parent.parent / "resources" / "cyq.js"
# MiniRacer 并发保护（官方不保证线程安全，CYQCalculator 有内部状态）
_cyq_lock = threading.Lock()


@lru_cache(maxsize=None)
def _cyq_engine() -> Any:
    """加载并缓存 CYQ JS 引擎（首次 eval ~50ms，后续 <1ms）。"""
    from py_mini_racer import MiniRacer
    js = MiniRacer()
    js.eval(_CYQ_JS_PATH.read_text(encoding="utf-8"))
    return js


def _fetch_klines_push2his(ctx: FetchContext, code: str, adjust: str) -> list[dict]:
    """push2his 取日 K 线，解析为 records（对齐 CYQ 输入格式）。

    空响应/网络失败返回 []（调用方走百度降级）。
    """
    market_code = 1 if code.startswith("6") else 0
    if not ctx.acquire():
        return []
    try:
        r = egress_request("get", ctx.source.egress_client, _KLINE_URL,
                           params={"secid": f"{market_code}.{code}",
                                   "fields1": "f1,f2,f3,f4,f5,f6",
                                   "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                                   "klt": "101", "fqt": adjust, "lmt": "210"},
                           headers={"User-Agent": _UA, "Referer": _REFERER}, timeout=15)
    except Exception:
        ctx.on_network_error()
        return []
    if r.status_code in (403, 429):
        ctx.on_failure(status=r.status_code, immediate=True)
        return []
    if r.status_code >= 500:
        ctx.on_failure(status=r.status_code)
        return []
    ctx.on_success()
    klines = []
    if r.ok:
        klines = (r.json().get("data") or {}).get("klines") or []
    records = []
    for line in klines:
        p = line.split(",")
        if len(p) < 11:
            continue
        records.append({
            "index": len(records), "date": p[0],
            "open": float(p[1]), "close": float(p[2]), "high": float(p[3]),
            "low": float(p[4]), "volume": float(p[5]), "volume_money": float(p[6]),
            "zf": float(p[7]), "zdf": float(p[8]), "zde": float(p[9]), "hsl": float(p[10]),
        })
    return records


def _fetch_klines_baidu(ctx: FetchContext, code: str) -> list[dict]:
    """百度日 K 降级（curl_cffi 直接取数，不走 eastmoney 熔断）。

    push2his 空响应/失败时继续取 K 线，避免上游空响应变成永久空结果。

    直接用 curl_cffi egress（百度的 egress_client），不经 ctx.acquire——push2his
    失败可能已熔断 eastmoney 组，baidu 是独立源（baidu 组），不应被 eastmoney 熔断
    连锁。baidu 的限流/熔断由其自己的 capability（baidu_kline）独立管理，降级链
    只借用其取数逻辑，不串用流量基础设施。
    """
    params = {"all": "1", "isIndex": "false", "isBk": "false", "isBlock": "false",
              "isFutures": "false", "isStock": "true", "newFormat": "1",
              "group": "quotation_kline_ab", "finClientType": "pc",
              "code": code, "start_time": "", "ktype": "1"}
    headers = {"Accept": "application/vnd.finance-web.v1+json",
               "Origin": "https://gushitong.baidu.com",
               "Referer": "https://gushitong.baidu.com/"}
    try:
        r = egress_request("get", "curl_cffi",
                           "https://finance.pae.baidu.com/selfselect/getstockquotation",
                           params=params, headers=headers, timeout=10)
    except Exception:
        return []
    try:
        d = r.json()
    except ValueError:
        return []
    if not isinstance(d, dict) or str(d.get("ResultCode", "")) != "0":
        return []
    md = (d.get("Result") or {}).get("newMarketData", {}) or {}
    keys = md.get("keys", []) or []
    records = []
    for line in (md.get("marketData", "") or "").split(";")[-210:]:
        row = dict(zip(keys, line.split(",")))
        try:
            close = float(row["close"])
            pre_close = float(row.get("preClose") or close)
            records.append({
                "index": len(records), "date": row["time"],
                "open": float(row["open"]), "close": close,
                "high": float(row["high"]), "low": float(row["low"]),
                "volume": float(row["volume"]), "volume_money": float(row["amount"]),
                "zf": float(row.get("range") or 0), "zdf": float(row.get("ratio") or 0),
                "zde": close - pre_close, "hsl": float(row.get("turnoverratio") or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return records


def _to_float(val) -> float | None:
    """CYQ 数值字段可能返回字符串（'10.38'）或 None，统一转 float。"""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


@capability(
    name="chip",
    domain="筹码",
    sources=[SourceMeta(name="eastmoney", group="eastmoney")],
    default_source="eastmoney",
    data_type="table",  # 最近 90 日筹码分布
    cache_policy="daily_settled",  # 日级（S 档，盘后定稿）
    supported_formats=["json", "csv", "md", "xlsx"],
)
def fetch_chip(ctx: FetchContext, code: str, adjust: str = "",
               **_unused) -> list[dict] | None:
    """筹码分布 + 主力成本（最近 90 日）。

    Args:
        code: 纯数字股票代码（如 "000001"）
        adjust: "" 不复权 / "qfq" 前复权 / "hfq" 后复权
    Returns:
        [{date, benefit_part(获利比例,小数), avg_cost(平均成本),
          pct90_low/high/concentration, pct70_low/high/concentration}, ...]
        最近 90 日。
    """
    adjust_map = {"qfq": "1", "hfq": "2", "": "0"}
    adj = adjust_map.get(adjust, "0")
    records = _fetch_klines_push2his(ctx, code, adj)
    if not records:
        # push2his 失败/空响应（网络错误会让 ctx.failed=True）。百度降级是有效的
        # 二级源，成功应清除失败态——否则 server.handle_capability 见 ctx.failed
        # 直接返回 502，无视百度已取到 K 线。清态让降级成功路径正常返回。
        ctx.failed = False
        ctx.last_status = None
        records = _fetch_klines_baidu(ctx, code)
    if not records:
        return None if ctx.failed else []

    js = _cyq_engine()
    rows = []
    for i in range(len(records)):
        # CYQCalculator 有内部状态（累积筹码），并发需串行
        with _cyq_lock:
            mcode = js.call("CYQCalculator", i, records)
        pc = mcode["percentChips"]
        rows.append({
            "date": records[i]["date"],
            "benefit_part": mcode["benefitPart"],
            "avg_cost": _to_float(mcode["avgCost"]),
            "pct90_low": _to_float(pc["90"]["priceRange"][0]),
            "pct90_high": _to_float(pc["90"]["priceRange"][1]),
            "pct90_concentration": pc["90"]["concentration"],
            "pct70_low": _to_float(pc["70"]["priceRange"][0]),
            "pct70_high": _to_float(pc["70"]["priceRange"][1]),
            "pct70_concentration": pc["70"]["concentration"],
        })
    return rows[-90:]
