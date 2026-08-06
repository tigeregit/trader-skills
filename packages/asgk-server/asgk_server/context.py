"""asgk_server.context — 能力函数的流量上下文 + 异常类型。

抽出独立模块避免循环导入：capabilities/*.py 需 FetchContext（出网时调
ctx.acquire 限流 + 反馈熔断），而 server.py 也要用这些类型。若 FetchContext
留在 server.py，capabilities 导入它会触发 server.py 还在初始化时的循环导入。

本模块只依赖 traffic（TokenBucket/CircuitBreaker）和 registry（SourceMeta），
无反向依赖，是依赖图的底层。
"""
from __future__ import annotations

from .registry import SourceMeta
from .traffic import CircuitBreaker, TokenBucket


class FetchContext:
    """能力函数的流量上下文：fetch 内部用它 acquire 限流、反馈熔断。

    fetch 约定：
      - 出网前调 ctx.acquire()（限流 + 熔断 canary 判定）
      - 成功调 ctx.on_success()，失败调 ctx.on_failure(status, immediate)
      - 或对 requests 异常用 ctx.on_network_error()
    """

    def __init__(self, group: str, bucket: TokenBucket, circuit: CircuitBreaker,
                 source_meta: SourceMeta, max_attempts: int):
        self.group = group
        self.bucket = bucket
        self.circuit = circuit
        self.source = source_meta
        self.max_attempts = max_attempts
        self.failed = False
        self.last_status: int | str | None = None

    def acquire(self) -> bool:
        """限流 + 熔断 canary 判定。返回 False 表示熔断中不可出网。"""
        self.bucket.acquire()
        return self.circuit.before_request()

    def on_success(self) -> None:
        self.circuit.success()
        self.last_status = 200

    def on_failure(self, status: int | None = None, *, immediate: bool = False) -> None:
        self.circuit.failure(immediate=immediate, status=status)
        self.last_status = status
        if immediate or status in (500, 502, 503, 504):
            self.failed = True

    def on_network_error(self) -> None:
        self.circuit.failure()
        self.failed = True
        self.last_status = "network"


class SourceBlocked(Exception):
    """熔断/状态闩打开，受控源不可出网。"""


class SourceUnhealthy(Exception):
    """指定源熔断或无健康源可用。"""
