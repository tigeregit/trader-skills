"""asgk._contract — 业务函数的元数据声明契约。

@source 装饰器让每个业务函数声明自己的缓存档位/指纹strip/数据源/CLI命令，
一处定义，驱动缓存、指纹日志、文档生成、CLI 注册。

设计原则：装饰器是纯声明，运行时零开销——不改变函数行为（tier 仍由函数体内
em_get 调用时传入），只把元数据存到函数对象属性上，供工具读取。
"""
from __future__ import annotations

from functools import wraps
from typing import Callable, Literal

Tier = Literal["P", "L", "S", "R", "N"]
Via = Literal["gateway", "direct"]


class SourceMeta:
    """业务函数的元数据。"""

    __slots__ = ("tier", "strip", "via", "cli", "name", "func")

    def __init__(self, tier: Tier, strip: list[str] | None, via: Via,
                 cli: str | None, name: str, func: Callable):
        self.tier = tier
        self.strip = strip
        self.via = via
        self.cli = cli
        self.name = name          # 如 "signal.eastmoney_concept_blocks"
        self.func = func

    def __repr__(self) -> str:
        return (f"SourceMeta(name={self.name!r}, tier={self.tier!r}, "
                f"via={self.via!r}, cli={self.cli!r})")


# 全局注册表：所有 @source 声明的函数元数据（供 CLI 注册/文档生成/离线分析遍历）
_REGISTRY: list[SourceMeta] = []


def source(tier: Tier, *, strip: list[str] | None = None,
           via: Via = "gateway", cli: str | None = None) -> Callable:
    """声明业务函数的元数据。

    Args:
        tier: 缓存档位 P/L/S/R/N（先验方案，gateway-design §3.4.6）
        strip: 响应哈希需剔除的动态字段（§3.4.7），None=无
        via: "gateway"(风控源经网关) / "direct"(腾讯/百度/新浪/mootdx 直连)
        cli: 对应 CLI 子命令名，None=不暴露为命令行
    """
    def decorator(func: Callable) -> Callable:
        mod = func.__module__.replace("asgk.", "") if func.__module__.startswith("asgk.") else func.__module__
        meta = SourceMeta(tier=tier, strip=strip, via=via, cli=cli,
                          name=f"{mod}.{func.__name__}", func=func)

        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        wrapper._asgk_meta = meta  # 挂到包装函数上供工具读取
        _REGISTRY.append(meta)
        return wrapper

    return decorator


def registry() -> list[SourceMeta]:
    """返回所有已声明 @source 的函数元数据（CLI 注册/文档生成用）。"""
    return list(_REGISTRY)
