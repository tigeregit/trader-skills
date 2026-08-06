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


# ── 交易时序：纯本地计算（不调服务端）─────────────────────────
# A股交易时段：周一~周五 09:25-11:30 / 13:00-15:00（含集合竞价 9:15-9:25）
#   - 09:15-09:30 集合竞价（可下单不可撤）
#   - 09:30-11:30 上午连续竞价
#   - 13:00-15:00 下午连续竞价（14:57-15:00 收盘集合竞价）
_INTRA_MORNING = (9, 25), (11, 30)   # 09:25 - 11:30
_INTRA_AFTERNOON = (13, 0), (15, 0)  # 13:00 - 15:00


def time_now() -> dict:
    """当前日期时间（本地时区，含星期、是否周末）。纯本地计算。"""
    import datetime as dt
    now = dt.datetime.now()
    return {
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()],
        "weekday_num": now.weekday(),  # 0=周一 ... 6=周日
        "is_weekend": now.weekday() >= 5,
    }


def trade_session() -> dict:
    """当前是否在交易时段（含盘前判断）。纯本地计算，不判节假日。

    判定区间（周一~周五）：
      pre_open   : 09:15-09:25  盘前集合竞价
      morning    : 09:25-11:30  上午连续竞价（可交易）
      midday     : 11:30-13:00  午间休市
      afternoon  : 13:00-15:00  下午连续竞价（可交易）
      closed     : 其余          闭市
    is_tradable=True 当且仅当 morning/afternoon（连续竞价可成交）。
    """
    import datetime as dt
    now = dt.datetime.now()
    if now.weekday() >= 5:
        return {"session": "closed", "is_tradable": False,
                "note": "周末闭市", "datetime": now.strftime("%Y-%m-%d %H:%M:%S")}
    t = (now.hour, now.minute)
    if (9, 15) <= t < (9, 25):
        session, tradable = "pre_open", False
    elif _INTRA_MORNING[0] <= t < _INTRA_MORNING[1]:
        session, tradable = "morning", True
    elif (11, 30) <= t < (13, 0):
        session, tradable = "midday", False
    elif _INTRA_AFTERNOON[0] <= t < _INTRA_AFTERNOON[1]:
        session, tradable = "afternoon", True
    else:
        session, tradable = "closed", False
    return {"session": session, "is_tradable": tradable,
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S")}


def time_status(trade_day_result: dict | None = None) -> dict:
    """合并 status：当前时间 + 是否交易时段 + 是否交易日。

    trade_day_result: 调用方可预先通过服务端拿到 trade_day 结果传入；
    若为 None 则标记为 unable_determine（避免 local 函数直接出网）。

    设计：本函数纯合并，不出网。CLI 的 _run_command 对 `time status` 做编排——
    先调服务端 calendar(trade_day) 拿结果，再调本函数合并。
    """
    now_info = time_now()
    session_info = trade_session()
    return {
        "datetime": now_info["datetime"],
        "date": now_info["date"],
        "weekday": now_info["weekday"],
        "is_weekend": now_info["is_weekend"],
        "session": session_info["session"],
        "is_tradable": session_info["is_tradable"],
        "is_trade_day": trade_day_result.get("is_trade_day") if trade_day_result else None,
        "trade_day_note": (trade_day_result.get("note") if trade_day_result
                           else "未判定（服务端不可达）"),
    }


# local_fn 名 → 函数 的分派表（commands.py 的 CmdSpec.local_fn 引用此处符号名）
LOCAL_FNS = {
    "forward_pe": forward_pe,
    "pe_digestion": pe_digestion,
    "calc_peg": calc_peg,
    "time_now": time_now,
    "trade_session": trade_session,
    "time_status": time_status,
}
