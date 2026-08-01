"""asgk.valuation — 估值计算（纯本地计算 + full_valuation 串联）。

移植自 ref/a-stock-data SKILL.md「估值计算公式」+「流程A」。按 asgk-contract.md：
  - forward_pe/pe_digestion/calc_peg 纯计算，无网络，无 tier
  - full_valuation 串联腾讯行情(直连) + 一致预期EPS，cli=valuation
"""
from __future__ import annotations

import math
import urllib.request

from asgk._contract import source


def forward_pe(price: float, eps_forecast: float) -> float:
    """前向PE = 当前股价 / 未来年度一致预期EPS。eps<=0 返回 inf。"""
    if eps_forecast <= 0:
        return float("inf")
    return price / eps_forecast


def pe_digestion(current_pe: float, cagr: float, target_pe: float = 30) -> float:
    """当前PE消化到目标PE需要多少年。

    target_pe 默认30x（A股成长股合理估值锚点）。cagr 用 下一年EPS/当年EPS-1。
    """
    if current_pe <= target_pe:
        return 0.0
    if cagr <= 0:
        return float("inf")
    return math.log(current_pe / target_pe) / math.log(1 + cagr)


def calc_peg(pe: float, cagr: float) -> float:
    """PEG = 前向PE / (CAGR*100)。<1便宜，1-1.5合理，>1.5贵。"""
    if cagr <= 0:
        return float("inf")
    return pe / (cagr * 100)


@source(tier="P", via="direct", cli="valuation")
def full_valuation(code: str) -> dict:
    """单票完整估值分析（串联腾讯行情 + 一致预期EPS）。

    Returns: name/price/mcap_yi/pe_ttm/pb/eps_cur/eps_next/pe_fwd/cagr_pct/peg/
    digest_years/analyst_count。EPS 相关字段在 ths_eps_forecast 不可用时为 None。
    """
    # 1. 腾讯实时行情（直连，GBK）
    prefix = "sh" if code.startswith(("6", "9")) else ("bj" if code.startswith("8") else "sz")
    req = urllib.request.Request(f"https://qt.gtimg.cn/q={prefix}{code}")
    req.add_header("User-Agent", "Mozilla/5.0")
    vals = urllib.request.urlopen(req, timeout=10).read().decode("gbk").split('"')[1].split("~")
    price = float(vals[3])
    pe_ttm = float(vals[39]) if vals[39] else 0
    pb = float(vals[46]) if vals[46] else 0
    mcap = float(vals[44])

    # 2. 机构一致预期EPS（ths_eps_forecast 尚未移植，try 容错）
    eps_cur = eps_next = None
    analyst_count = 0
    try:
        from asgk.reports import ths_eps_forecast
        forecast = ths_eps_forecast(code)
        if hasattr(forecast, "to_dict"):
            rows = forecast.to_dict("records")
        else:
            rows = list(forecast or [])
        rows.sort(key=lambda row: row.get("年度") or row.get("year") or 0)
        if rows:
            def _pick(row, name):
                for key, value in row.items():
                    if name in str(key):
                        return value
                return None
            r0 = rows[0]
            v = _pick(r0, "均值")
            eps_cur = float(v) if v is not None and str(v) != "nan" else None
            cnt = _pick(r0, "预测机构数")
            analyst_count = int(cnt) if cnt is not None and str(cnt) != "nan" else 0
            if len(rows) >= 2:
                vn = _pick(rows[1], "均值")
                eps_next = float(vn) if vn is not None and str(vn) != "nan" else None
    except (ImportError, Exception):
        pass  # ths_eps_forecast 未移植或请求失败，EPS 字段留 None

    # 3. 估值指标
    pe_fwd = price / eps_cur if eps_cur else float("inf")
    cagr = (eps_next / eps_cur - 1) if (eps_cur and eps_next) else 0
    peg = pe_fwd / (cagr * 100) if cagr > 0 else float("inf")
    digest = (math.log(pe_fwd / 30) / math.log(1 + cagr)
              if pe_fwd > 30 and cagr > 0 else 0)

    return {
        "name": vals[1], "price": price, "mcap_yi": mcap,
        "pe_ttm": pe_ttm, "pb": pb,
        "eps_cur": eps_cur, "eps_next": eps_next,
        "pe_fwd": round(pe_fwd, 1) if eps_cur else None,
        "cagr_pct": round(cagr * 100, 0) if cagr else None,
        "peg": round(peg, 2) if peg != float("inf") else None,
        "digest_years": round(digest, 1),
        "analyst_count": analyst_count,
    }
