"""asgk_server.capabilities.calendar — 交易日历能力。

交易日判定：用 mootdx 拿上证指数(000001)日K反推真实交易日（含节假日剔除）。
K线只在交易日产生，故 index_bars 的 datetime 列即交易日历。

数据范围：历史 ~近期（K线已产生之日）。未来日期不支持（无可靠节假日源）。

缓存：cache_policy=definitive（30天 persist 落盘）。交易日历极低频变化，
获取一次长期复用；30 天后自动重拉以纳入新产生的交易日。
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..context import FetchContext
from ..registry import SourceMeta, capability

# 上证指数（用于反推交易日历）。market=1 是指数，frequency=9 是日线。
_CALENDAR_INDEX_CODE = "000001"
_CALENDAR_INDEX_MARKET = 1
_CALENDAR_INDEX_FREQUENCY = 9
# 一次拉够多的交易日覆盖历史查询（8000 个交易日 ≈ 32 年）
_CALENDAR_OFFSET = 8000


def _get_calendar_client():
    """获取 mootdx client 用于拉交易日历。

    显式指定已知能返回指数数据的服务器（180.153.18.172:80），不复用
    Quotes.factory 或 mootdx._get_client——后两者依赖 bestip 探测，
    在服务端长进程里可能缓存了不返回 index_bars 的差服务器。
    实测 180.153.18.172:80（TDX 心海主站）稳定返回上证指数日K。
    """
    from mootdx.quotes import StdQuotes
    return StdQuotes(server=['180.153.18.172', 80], bestip=False, timeout=15)

# 上证指数（用于反推交易日历）。market=1 是指数，frequency=9 是日线。
_CALENDAR_INDEX_CODE = "000001"
_CALENDAR_INDEX_MARKET = 1
_CALENDAR_INDEX_FREQUENCY = 9
# 一次拉够多的交易日覆盖历史查询（8000 个交易日 ≈ 32 年）
_CALENDAR_OFFSET = 8000


@capability(
    name="calendar",
    domain="交易时序",
    sources=[SourceMeta(name="mootdx", group="mootdx")],
    default_source="mootdx",
    data_type="kv",
    cache_policy="definitive",  # 30天 persist：交易日历极低频变化
    supported_formats=["json", "md"],
)
def fetch_calendar(ctx: FetchContext, calendar_type: str,
                   date: str | None = None, **_unused) -> Any:
    """交易时序查询。calendar_type ∈ {trade_days, trade_day}。

    trade_days: 返回全部已知交易日列表（历史~近期）。供客户端判定任意历史日期。
    trade_day: 判定单个 date 是否交易日。需 date（YYYY-MM-DD）。
               未来日期（晚于最新已知交易日）返回 not_supported=true。

    Args:
        ctx: 上下文（限流/熔断/缓存反馈）。
        calendar_type: trade_days | trade_day。
        date: trade_day 时指定要判定的日期（YYYY-MM-DD）。
    """
    if not ctx.acquire():
        return None
    try:
        client = _get_calendar_client()
    except Exception:
        ctx.on_network_error()
        return None

    try:
        df = client.index_bars(frequency=_CALENDAR_INDEX_FREQUENCY,
                               market=_CALENDAR_INDEX_MARKET,
                               code=_CALENDAR_INDEX_CODE,
                               start=0, offset=_CALENDAR_OFFSET)
        if df is None or len(df) == 0:
            ctx.on_success()
            return {"trade_days": [], "latest": None,
                    "note": "未能获取交易日数据"}
        # datetime 列形如 "2026-08-06 15:00"，取日期部分
        trade_days = sorted({
            str(d).strip()[:10] for d in df["datetime"].tolist()
            if str(d).strip()
        })
        latest = trade_days[-1] if trade_days else None
        ctx.on_success()

        if calendar_type == "trade_days":
            return {"trade_days": trade_days, "earliest": trade_days[0],
                    "latest": latest, "count": len(trade_days)}

        if calendar_type == "trade_day":
            if not date:
                return {"error": "trade_day 需要 date 参数（YYYY-MM-DD）"}
            today = dt.date.today().isoformat()
            # 未来日期（晚于今天）：不支持
            if date > today:
                return {"date": date, "is_trade_day": None,
                        "not_supported": True,
                        "note": f"未来日期不支持（无法预知节假日）",
                        "latest_known": latest}
            # 今天/历史日期：若在交易日历里 → 确定；今天不在历（尚未收盘产生K线）
            # 则按工作日规则兜底，标注「待确认」
            trade_days_set = set(trade_days)
            if date in trade_days_set:
                return {"date": date, "is_trade_day": True,
                        "latest_known": latest}
            # 不在交易日历里：判断是「过去的非交易日」还是「今天尚未收盘」
            try:
                d_obj = dt.date.fromisoformat(date)
            except ValueError:
                return {"date": date, "is_trade_day": False,
                        "note": "日期格式错误（需 YYYY-MM-DD）",
                        "latest_known": latest}
            if d_obj.weekday() >= 5:
                # 周末：确定非交易日
                return {"date": date, "is_trade_day": False,
                        "note": "周末", "latest_known": latest}
            if date == today and latest and date > latest:
                # 今天是工作日但K线尚未产生（盘前/盘中）→ 工作日兜底
                return {"date": date, "is_trade_day": True,
                        "tentative": True,
                        "note": "今日尚未收盘，按工作日判定（节假日待确认）",
                        "latest_known": latest}
            # 过去的工作日但不在交易日历 → 节假日
            return {"date": date, "is_trade_day": False,
                    "note": "节假日", "latest_known": latest}

        return {"error": f"未知 calendar_type: {calendar_type}，支持 trade_days|trade_day"}
    except Exception:
        ctx.on_network_error()
        return None
