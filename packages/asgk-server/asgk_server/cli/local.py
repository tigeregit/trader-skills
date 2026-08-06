"""asgk_server.cli.local — 纯本地计算子命令（不调服务端）。

这几個估值计算函数无需网络/上游，直接在 CLI 内部算。移植自旧客户端
``asgk.valuation``（forward_pe/pe_digestion/calc_peg），逻辑零改动。

full_valuation 原本串联行情+EPS，现已在服务端 quote 能力实现（cli 研报 valuation
子命令调服务端），不在此处。
"""
from __future__ import annotations

import math


def forward_pe(price: float, eps_forecast: float) -> dict:
    """前向PE = 当前股价 / 未来年度一致预期EPS。eps<=0 返回 inf。"""
    if eps_forecast <= 0:
        return {"forward_pe": float("inf"), "note": "eps_forecast<=0"}
    return {"forward_pe": price / eps_forecast}


def pe_digestion(current_pe: float, cagr: float, target_pe: float = 30) -> dict:
    """当前PE消化到目标PE需要多少年。

    target_pe 默认30x（A股成长股合理估值锚点）。cagr 用 下一年EPS/当年EPS-1。
    """
    if current_pe <= target_pe:
        return {"years": 0.0, "note": "当前PE已低于目标"}
    if cagr <= 0:
        return {"years": float("inf"), "note": "cagr<=0，无法消化"}
    years = math.log(current_pe / target_pe) / math.log(1 + cagr)
    return {"years": years, "current_pe": current_pe,
            "target_pe": target_pe, "cagr": cagr}


def calc_peg(pe: float, cagr: float) -> dict:
    """PEG = 前向PE / (CAGR*100)。<1便宜，1-1.5合理，>1.5贵。"""
    if cagr <= 0:
        return {"peg": float("inf"), "note": "cagr<=0"}
    return {"peg": pe / (cagr * 100)}


# local_fn 名 → 函数 的分派表（commands.py 的 CmdSpec.local_fn 引用此处符号名）
LOCAL_FNS = {
    "forward_pe": forward_pe,
    "pe_digestion": pe_digestion,
    "calc_peg": calc_peg,
}
