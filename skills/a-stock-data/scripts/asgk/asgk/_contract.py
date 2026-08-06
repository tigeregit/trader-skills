"""asgk._contract — 业务函数的元数据声明契约。

@source 装饰器让每个业务函数声明自己的缓存档位、指纹 strip、数据源、数据类型和
可选调用入口标识，供缓存、指纹日志、格式化和工具发现使用。

设计原则：装饰器是纯声明 + 格式化注入，运行时零开销——不改变函数取数行为
（tier 仍由函数体内 em_get 调用时传入），只：
  1. 把元数据存到函数对象属性上，供工具读取
  2. 拦截 format/output/path 可选参数（§3.5 格式化层），不传时行为完全不变

格式化注入（§3.5）：调用方传 format= 时，装饰器在业务函数返回结构化数据后过
_format + _output；不传 format 时原样返回（零破坏，45 个现有调用方无感）。
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Literal

Tier = Literal["P", "L", "S", "R", "N"]
Via = Literal["gateway", "direct"]
DataType = Literal["kv", "table", "series", "text", "document"]

# data_type 默认支持的格式（§3.5 矩阵），未显式声明 supported_formats 时用此
_DEFAULT_FORMATS: dict[str, list[str]] = {
    "table": ["json", "csv", "md", "xlsx"],
    "kv": ["json", "md"],
    "series": ["json", "csv", "md", "xlsx"],
    "text": ["json", "md", "plain"],
    "document": [],
}


class SourceMeta:
    """业务函数的元数据。"""

    __slots__ = ("tier", "strip", "via", "cli", "name", "func", "wrapped",
                 "data_type", "supported_formats")

    def __init__(self, tier: Tier, strip: list[str] | None, via: Via,
                 cli: str | None, name: str, func: Callable,
                 data_type: DataType | None = None,
                 supported_formats: list[str] | None = None,
                 wrapped: Callable | None = None):
        self.tier = tier
        self.strip = strip
        self.via = via
        self.cli = cli
        self.name = name          # 如 "signal.eastmoney_concept_blocks"
        self.func = func          # 原始函数（无格式化）
        self.wrapped = wrapped    # @source 包装后的函数（含格式化/交付注入）
        self.data_type = data_type
        self.supported_formats = supported_formats

    def formats(self) -> list[str]:
        """该函数支持的格式（显式声明优先，否则按 data_type 默认矩阵）。"""
        if self.supported_formats is not None:
            return self.supported_formats
        if self.data_type is not None:
            return _DEFAULT_FORMATS.get(self.data_type, [])
        return []

    def __repr__(self) -> str:
        return (f"SourceMeta(name={self.name!r}, tier={self.tier!r}, "
                f"via={self.via!r}, cli={self.cli!r}, data_type={self.data_type!r})")


# 全局注册表：所有 @source 声明的函数元数据，供工具发现和离线分析遍历
_REGISTRY: list[SourceMeta] = []


def source(tier: Tier, *, strip: list[str] | None = None,
           via: Via = "gateway", cli: str | None = None,
           data_type: DataType | None = None,
           supported_formats: list[str] | None = None) -> Callable:
    """声明业务函数的元数据 + 注入格式化层（§3.5）。

    Args:
        tier: 缓存档位 P/L/S/R/N
        strip: 响应哈希需剔除的动态字段，None=无
        via: "gateway"(风控源经网关) / "direct"(腾讯/百度/新浪/mootdx 直连)
        cli: 可选调用入口标识，None=不声明
        data_type: 数据类型 kv/table/series/text/document（驱动格式校验，§3.5）
        supported_formats: 显式声明支持的格式（默认按 data_type 矩阵）
    """
    def decorator(func: Callable) -> Callable:
        mod = func.__module__.replace("asgk.", "") if func.__module__.startswith("asgk.") else func.__module__
        meta = SourceMeta(tier=tier, strip=strip, via=via, cli=cli,
                          name=f"{mod}.{func.__name__}", func=func,
                          data_type=data_type, supported_formats=supported_formats)

        @wraps(func)
        def wrapper(*args, **kwargs):
            # 拦截格式化/交付控制参数（不传给业务函数）
            fmt = kwargs.pop("format", None)
            output = kwargs.pop("output", "return")
            path = kwargs.pop("path", None)
            result = func(*args, **kwargs)
            # 不传 format → 原样返回（零破坏）
            if fmt is None:
                return result
            # 传了 format → 过格式化层 + 交付层
            return _apply_format(result, meta, fmt, output, path)

        wrapper._asgk_meta = meta  # 挂到包装函数上供工具读取
        meta.wrapped = wrapper  # 供 CLI 调用（含格式化注入）
        _REGISTRY.append(meta)
        return wrapper

    return decorator


def _apply_format(result: Any, meta: SourceMeta,
                  fmt: str, output: str, path: str | None) -> Any:
    """对业务函数返回值过格式化层 + 交付层（§3.5）。

    data_type 未声明时按返回类型推断（dict→kv, list→table, str→text）。
    """
    from asgk._format import format_data, validate
    from asgk._output import deliver

    data_type = meta.data_type or _infer_data_type(result)
    validate(data_type, fmt)
    formatted = format_data(result, data_type, fmt)
    return deliver(formatted, output, path, fmt)


def _infer_data_type(data: Any) -> str:
    """按返回类型推断 data_type（函数未声明 data_type 时兜底）。"""
    if isinstance(data, dict):
        return "kv"
    if isinstance(data, list):
        return "table"
    if isinstance(data, str):
        return "text"
    return "kv"  # 兜底


def registry() -> list[SourceMeta]:
    """返回所有已声明 @source 的函数元数据。"""
    return list(_REGISTRY)
