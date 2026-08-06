"""asgk.chip — 筹码层（筹码分布 + 主力成本）。

移植自 akshare stock_cyq_em（snapshot fcdbf25）。
  - K 线获取：push2his（经网关 em_get），空响应时降级百度，取近 210 根
  - CYQ 计算：vendor JS（py_mini_racer 执行 CYQCalculator，纯数学无 DOM）
  - 返回最近 90 日筹码分布
  - @source 档位：S（日级）
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from py_mini_racer import MiniRacer

from asgk._contract import source
from asgk.em_proxy import em_get

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"
_VENDOR_DIR = Path(__file__).parent / "_vendor"


@lru_cache(maxsize=None)
def _cyq_engine() -> MiniRacer:
    """加载并缓存 CYQ JS 引擎（首次 eval ~50ms，后续 <1ms）。

    lru_cache(maxsize=None) 确保只加载一次（maxsize=1 是旧版的错误）。
    MiniRacer context 的线程安全待并发测试验证（阶段4），必要时加锁。
    """
    js = MiniRacer()
    js.eval((_VENDOR_DIR / "cyq.js").read_text(encoding="utf-8"))
    return js


def _s(val) -> str:
    return val or ""


@source(tier="S", via="gateway", data_type="table")
def chip_distribution(symbol: str, adjust: str = "") -> list[dict]:
    """筹码分布 + 主力成本（单股，最近 90 日）。

    移植自 akshare stock_cyq_em。push2his 拉 K 线 + 本地 CYQ JS 计算。

    Args:
        symbol: 纯数字股票代码（如 "000001"），不带市场前缀（内部按首字符判定市场）
        adjust: 复权，"" 不复权 / "qfq" 前复权 / "hfq" 后复权
    Returns:
        [{date, benefit_part(获利比例,小数 0.5=50%),
          avg_cost(平均成本,元),
          pct90_low, pct90_high(90%成本区间,元), pct90_concentration(90%集中度),
          pct70_low, pct70_high(70%成本区间,元), pct70_concentration(70%集中度)}, ...]
        最近 90 日
    """
    adjust_map = {"qfq": "1", "hfq": "2", "": "0"}
    if adjust not in adjust_map:
        raise ValueError(f"adjust 取值: ''(不复权)/'qfq'(前复权)/'hfq'(后复权)，得到: {adjust!r}")
    market_code = 1 if symbol.startswith("6") else 0
    r = em_get("https://push2his.eastmoney.com/api/qt/stock/kline/get",
               params={"secid": f"{market_code}.{symbol}",
                       "fields1": "f1,f2,f3,f4,f5,f6",
                       "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                       "klt": "101", "fqt": adjust_map[adjust], "lmt": "210"},
               headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
               timeout=15, tier="S")
    klines = []
    if r.ok:
        payload = r.json()
        data = payload.get("data") or {}
        klines = data.get("klines") or []
    # 解析 K 线为 records（对齐 akshare CYQ 输入格式）
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

    # push2his 在部分网络/时段返回 rc=0,data=null；用已验证的百度日 K 继续
    # 本地 CYQ 计算，避免把上游空响应变成 AttributeError 或永久空结果。
    if not records:
        from asgk.quote import baidu_kline_with_ma

        baidu = baidu_kline_with_ma(symbol)
        keys = baidu.get("keys") or []
        for line in (baidu.get("rows") or [])[-210:]:
            row = dict(zip(keys, line.split(",")))
            try:
                close = float(row["close"])
                pre_close = float(row.get("preClose") or close)
                records.append({
                    "index": len(records), "date": row["time"],
                    "open": float(row["open"]), "close": close,
                    "high": float(row["high"]), "low": float(row["low"]),
                    "volume": float(row["volume"]),
                    "volume_money": float(row["amount"]),
                    "zf": float(row.get("range") or 0),
                    "zdf": float(row.get("ratio") or 0),
                    "zde": close - pre_close,
                    "hsl": float(row.get("turnoverratio") or 0),
                })
            except (KeyError, TypeError, ValueError):
                continue

    js = _cyq_engine()
    rows = []
    for i in range(len(records)):
        mcode = js.call("CYQCalculator", i, records)
        pc = mcode["percentChips"]
        # CYQ 部分数值字段返回字符串（如 avgCost="10.38"），统一转 float
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
    return rows[-90:]  # 最近 90 日（对齐 akshare）


def _to_float(val) -> float | None:
    """CYQ 数值字段可能返回字符串（'10.38'）或 None，统一转 float。"""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
