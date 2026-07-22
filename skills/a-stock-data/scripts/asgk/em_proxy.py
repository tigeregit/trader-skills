"""asgk.em_proxy — 统一请求入口 em_get，走网关/直连自适应。

接口与上游 ref/a-stock-data 的 em_get 兼容（零改动迁移）：
    em_get(url, params=None, headers=None, timeout=15, **kwargs)

新增可选 tier 参数（分档先验方案，见 gateway-design.md §3.4.6）：
    em_get(url, params=..., tier="S")   # 板块归属→S档

行为：
    - 设了 ASGK_GW 环境变量 → 请求转发到网关（全局限流+缓存），tier 放 X-Cache-Tier 头
    - 没设 → 直连上游（向后兼容；保留进程内限流作 fallback）
"""
from __future__ import annotations

import os
import random
import time

import requests

_GW = os.environ.get("ASGK_GW")  # 如 http://127.0.0.1:7700；未设则直连
_TIER_HEADER = "X-Cache-Tier"

# 直连时的进程内限流（仅 fallback 用；走网关时限流在网关侧全局生效）
_MIN_INTERVAL = 1.0
_last_call = [0.0]


def _direct_throttle():
    """直连模式下的进程内限流（对齐上游 EM_MIN_INTERVAL）。"""
    wait = _MIN_INTERVAL - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    _last_call[0] = time.time()


def em_get(url: str, params: dict | None = None, headers: dict | None = None,
           timeout: int = 15, tier: str | None = None, **kwargs) -> requests.Response:
    """统一请求入口。

    Args:
        url: 上游完整 URL
        params: query 参数
        headers: 请求头
        timeout: 超时秒
        tier: 缓存档位 P/L/S/R/N（仅走网关时生效）；None 则网关走兜底规则
    """
    h = dict(headers or {})
    if tier:
        h[_TIER_HEADER] = tier

    if _GW:
        # 走网关：u=原始URL，其余参数转发；tier 在头里
        return requests.get(
            _GW,
            params={"u": url, **(params or {})},
            headers=h,
            timeout=timeout,
            **kwargs,
        )
    # 直连 fallback
    _direct_throttle()
    return requests.get(url, params=params, headers=h, timeout=timeout, **kwargs)
