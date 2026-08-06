"""asgk_server.registry — 能力注册表（§3.2）。

每个能力声明元数据（数据源/限流组/容灾/数据形态），驱动选源/限流/缓存/容灾。
服务端持有全部上游知识，客户端只发语义请求。

@capability 装饰器把业务函数登记进全局注册表；CapabilityMeta 描述：
  - sources：该能力支持的全部源（驱动 GET /v1/sources），按优先级排序
  - default_source：不指定 source 时的首选（熔断则降级下一健康源）
  - data_type：数据形态（kv/table/series/text/doc）驱动客户端格式校验
  - cache_policy：缓存分档（T1.5 接入，definitive/quarterly/.../realtime）

T1 阶段只搭注册框架；真实能力（quote/kline/...）在 T2~T10 各梯队填充。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# 数据形态：驱动客户端格式校验与默认输出格式（§3.2 data_type）
_DATA_TYPES = {"kv", "table", "series", "text", "doc"}


@dataclass
class SourceMeta:
    """一个数据源的元数据。

    name: 源标识（tencent/sina/eastmoney/mootdx/...），客户端可用 source= 指定。
    group: 该源所属的限流组（对应 config.toml 的 [[group]] name），驱动令牌桶+熔断。
    egress_client: 出网客户端（"requests"默认 / "curl_cffi" 百度指纹）。
    healthy: 运行时健康度，由熔断器实时更新；自动选源时跳过不健康的源。
    """

    name: str
    group: str
    egress_client: str = "requests"
    healthy: bool = True


@dataclass
class CapabilityMeta:
    """一个能力的元数据（§3.2）。由 @capability 声明，驱动服务端全部决策。"""

    name: str
    domain: str
    sources: list[SourceMeta]
    default_source: str
    data_type: str
    fallback: list[str] = field(default_factory=list)
    # cache_policy 在 T1.5 接入（definitive/quarterly/daily_settled/
    # daily_volatile/realtime/streaming，§3.6c 六类）；T1 先留位，默认 realtime(no-cache)
    cache_policy: str = "realtime"
    supported_formats: list[str] = field(default_factory=lambda: ["json"])

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("capability name must not be empty")
        if self.data_type not in _DATA_TYPES:
            raise ValueError(
                f"capability {self.name}: invalid data_type={self.data_type!r}, "
                f"must be one of {sorted(_DATA_TYPES)}"
            )
        if not self.sources:
            raise ValueError(f"capability {self.name}: must declare at least one source")
        names = [s.name for s in self.sources]
        if len(names) != len(set(names)):
            raise ValueError(f"capability {self.name}: duplicate source names {names}")
        if self.default_source not in names:
            raise ValueError(
                f"capability {self.name}: default_source={self.default_source!r} "
                f"not in sources {names}"
            )
        if self.fallback and self.fallback[0] != self.default_source:
            # fallback 链第一环应与 default_source 一容（语义：default 熔断→fallback[1:]）
            # 这里只做结构校验，不强求相等——调用方可能想表达不同的降级语义。
            pass

    def source(self, name: str) -> SourceMeta:
        """按名取源；不存在则 KeyError（服务端据此返回 400 unknown source）。"""
        for s in self.sources:
            if s.name == name:
                return s
        raise KeyError(name)

    def source_names(self) -> list[str]:
        return [s.name for s in self.sources]


# ── 全局注册表 ────────────────────────────────────────────────
_REGISTRY: dict[str, tuple[CapabilityMeta, Callable]] = {}


def capability(
    *,
    name: str,
    domain: str,
    sources: list[SourceMeta],
    default_source: str,
    data_type: str,
    fallback: list[str] | None = None,
    cache_policy: str = "realtime",
    supported_formats: list[str] | None = None,
) -> Callable[[Callable], Callable]:
    """声明一个能力并登记进全局注册表。

    被装饰的 fetch 函数签名约定：fetch(**semantic_params, source: str | None)。
    服务端按 source（显式或自动选源）解析出健康 SourceMeta 后调用 fetch。
    """
    meta = CapabilityMeta(
        name=name, domain=domain, sources=sources,
        default_source=default_source, data_type=data_type,
        fallback=fallback or [], cache_policy=cache_policy,
        supported_formats=supported_formats or ["json"],
    )

    def decorator(fn: Callable) -> Callable:
        if name in _REGISTRY:
            raise ValueError(f"capability {name!r} already registered")
        _REGISTRY[name] = (meta, fn)
        return fn

    return decorator


def get_capability(name: str) -> tuple[CapabilityMeta, Callable]:
    """取已注册能力；不存在则 KeyError（服务端据此返回 404 unknown capability）。"""
    if name not in _REGISTRY:
        raise KeyError(name)
    return _REGISTRY[name]


def list_capabilities() -> dict[str, CapabilityMeta]:
    """全部已注册能力的元数据（驱动 GET /v1/sources 不带 capability 时）。"""
    return {name: meta for name, (meta, _fn) in _REGISTRY.items()}


def clear_registry() -> None:
    """清空注册表（仅测试用，避免跨用例污染）。"""
    _REGISTRY.clear()
