"""asgk.em_proxy — 统一请求入口 em_get，走网关/直连自适应。

接口与上游 ref/a-stock-data 的 em_get 兼容（零改动迁移）：
    em_get(url, params=None, headers=None, timeout=15, **kwargs)

新增可选 tier 参数（分档先验方案，见 gateway-design.md §3.4.6）：
    em_get(url, params=..., tier="S")   # 板块归属→S档

行为：
    - 设了 ASGK_GW → 请求转发到网关（全局限流+缓存），tier 放 X-Cache-Tier 头
    - 没设 → 直连上游（向后兼容；保留进程内限流作 fallback）

ASGK_GW 的来源（优先级从高到低）：
    1. 环境变量 ASGK_GW（最高，部署时 systemd/container env 用这个）
    2. .env 文件里的 ASGK_GW（开发用；从 cwd 或 ASGK_ENV 指定路径加载）
环境变量优先于 .env，两者都未设则直连。
"""
from __future__ import annotations

import os
import random
import time
from pathlib import Path

import requests


def _load_dotenv() -> None:
    """从 .env 加载环境变量到 os.environ（不覆盖已设的）。

    查找顺序：ASGK_ENV 指定路径 → cwd/.env → 向上最多3级目录找 .env。
    仅填补 os.environ 中未设置的键（环境变量优先级高于 .env）。
    """
    candidates: list[Path] = []
    env_path = os.environ.get("ASGK_ENV")
    if env_path:
        candidates.append(Path(env_path))
    cwd = Path.cwd()
    candidates.append(cwd / ".env")
    for _ in range(3):
        cwd = cwd.parent
        candidates.append(cwd / ".env")

    for p in candidates:
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip("'\"")
            if key and key not in os.environ:  # 不覆盖已设的环境变量
                os.environ[key] = val
        break  # 只用第一个找到的 .env


_load_dotenv()
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
