"""asgk_server.cache_policy — 六类（+文档）数据型 → 缓存策略映射（§3.6c）。

按数据更新特性分类型，每类驱动 TTL / 存储方式 / 是否落盘 / 粒度，取代 sgw 的
五档 tier（P/L/S/R/N）一刀切 TTL。

能力注册表的 `cache_policy` 字段声明所属类型（如 definitive/quarterly/realtime），
本模块把类型名映射到具体的缓存行为参数（CachePolicy）。

日级型(盘后定稿) 的 TTL 随交易时段变化：盘中 no-cache（避免脏数据），盘后 12h。
判定交易时段的方法由调用方注入（server 持有 config 的 session 判断），本模块只
暴露 policy 描述，不自己判时段——is_intraday_fn 在 resolve 时传入。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

# 合法的 cache_policy 取值（§3.6c 六类 + §3.7 文档型）
CACHE_POLICIES = frozenset({
    "definitive",        # 定稿型：公告/分红/F10/互动易
    "quarterly",         # 季度型：财报三表/股东户数/业绩预告
    "daily_settled",     # 日级型(盘后定稿)：龙虎榜/融资融券/大宗/板块
    "daily_volatile",    # 日级型(随时变)：研报评级/质押/解禁
    "realtime",          # 实时型：行情/K线/盘口/资金流/涨停池
    "streaming",         # 流式型：新闻电报
    "document",          # 文档型：公告PDF原文/研报PDF/年报（§3.7）
})


@dataclass(frozen=True)
class CachePolicy:
    """一个 cache_policy 的具体行为参数。

    ttl_seconds: 缓存存活秒数；0 表示 no-cache（但仍走 singleflight 合并）。
        None 表示需要动态计算（如 daily_settled 随交易时段变）。
    persist: 是否落盘（JSON 文件）。只有更新频率低、值定稿的类型才落盘，
        避免盘后定稿/实时型频繁失效污染磁盘。
    structured: True=存解析后的 dict/list（结构化）；False=存原始 bytes（文档型）。
        文档型（PDF/xlsx）本质是文件，格式化层不适用，存原始 bytes。
    """

    ttl_seconds: Optional[int]
    persist: bool
    structured: bool


# ── 六类（+文档）→ CachePolicy 映射（§3.6c 表格）──────────────
# 日级型(盘后定稿) 的 TTL 在 resolve 时按交易时段决定（盘中0/盘后12h），这里
# 标 None 表示动态；其余类型 TTL 固定。
_POLICIES: dict[str, CachePolicy] = {
    "definitive":     CachePolicy(ttl_seconds=30 * 86400, persist=True,  structured=True),   # 30天
    "quarterly":      CachePolicy(ttl_seconds=86400,       persist=True,  structured=True),   # 1天
    "daily_settled":  CachePolicy(ttl_seconds=None,        persist=False, structured=True),   # 盘中0/盘后12h
    "daily_volatile": CachePolicy(ttl_seconds=3600,        persist=False, structured=True),   # 1h
    "realtime":       CachePolicy(ttl_seconds=0,           persist=False, structured=True),   # no-cache
    "streaming":      CachePolicy(ttl_seconds=0,           persist=False, structured=True),   # no-cache
    "document":       CachePolicy(ttl_seconds=30 * 86400,  persist=True,  structured=False),  # 30天，原始bytes
}

# daily_settled 的两个时段 TTL（§3.6c：盘中 no-cache，盘后定稿 12h）
_DAILY_SETTLED_INTRADAY_TTL = 0
_DAILY_SETTLED_AFTERCLOSE_TTL = 12 * 3600


def resolve_ttl(policy: str, is_intraday_fn: Optional[Callable[[], bool]] = None) -> int:
    """把 cache_policy 解析为具体 TTL 秒数。

    daily_settled 随交易时段变：盘中 0（no-cache），盘后 12h。需要调用方提供
    is_intraday_fn（server 持有 config.session 判断）；不提供则按盘后（保守可缓存）。
    其余类型 TTL 固定，直接返回。
    """
    if policy not in _POLICIES:
        # 未知 policy 保守按 realtime（no-cache），宁缺毋滥
        return 0
    cp = _POLICIES[policy]
    if cp.ttl_seconds is not None:
        return cp.ttl_seconds
    # daily_settled：动态
    if policy == "daily_settled":
        if is_intraday_fn is not None and is_intraday_fn():
            return _DAILY_SETTLED_INTRADAY_TTL
        return _DAILY_SETTLED_AFTERCLOSE_TTL
    return 0


def should_persist(policy: str) -> bool:
    """该 policy 是否落盘（JSON 文件持久化）。"""
    cp = _POLICIES.get(policy)
    return cp.persist if cp is not None else False


def is_structured(policy: str) -> bool:
    """该 policy 是否存结构化数据（True）vs 原始 bytes（False，文档型）。"""
    cp = _POLICIES.get(policy)
    return cp.structured if cp is not None else True


def describe(policy: str) -> Optional[CachePolicy]:
    """取 policy 的描述对象（测试/调试用）。"""
    return _POLICIES.get(policy)
