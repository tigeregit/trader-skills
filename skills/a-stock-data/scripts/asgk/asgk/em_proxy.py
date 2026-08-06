"""asgk.em_proxy — 统一请求入口 em_get，风控源必经网关。

调用接口：
    em_get(url, params=None, headers=None, timeout=15, **kwargs)
    em_get(url, params=..., method="POST", json={...}, tier="R")  # POST+JSON

可选 tier 参数声明缓存档位：
    em_get(url, params=..., tier="S")   # 板块归属→S档

可选 method/json 参数支持 POST+JSON 接口（如东财人气榜 emappdata）：
    em_get(url, method="POST", json=body, tier="R")  # body 经网关透传并进 cache key

行为：
    - 设了 ASGK_GW → 请求转发到网关（全局限流+缓存），tier 放 X-Cache-Tier 头
      · GET：?u=url&k=v，params 随 query 转发
      · POST：?u=url，json body 放请求体转发（网关按 method+body 走上游）
    - 没设 ASGK_GW → **默认抛异常**（禁止风控源直连，杜绝忘配网关被封 IP）

ASGK_GW 的来源（优先级从高到低）：
    1. 环境变量 ASGK_GW（最高，部署时 systemd/container envfile 用这个）
    2. .env 文件里的 ASGK_GW（从 cwd 或 ASGK_ENV 指定路径加载）
环境变量优先于 .env。

em_get 只被风控源（东财/同花顺）调用。直连源（腾讯/百度/新浪/mootdx/巨潮）
不走 em_get，不受此约束——它们本就直连。
"""
from __future__ import annotations

import os
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
_GW = os.environ.get("ASGK_GW")  # 如 http://127.0.0.1:7700；未设则失败关闭
_TIER_HEADER = "X-Cache-Tier"

def em_get(url: str, params: dict | None = None, headers: dict | None = None,
           timeout: int = 15, tier: str | None = None, *,
           method: str = "GET", json: dict | None = None, **kwargs) -> requests.Response:
    """统一请求入口。

    Args:
        url: 上游完整 URL
        params: query 参数
        headers: 请求头
        timeout: 超时秒
        tier: 缓存档位 P/L/S/R/N（仅走网关时生效）；None 则网关走兜底规则
        method: HTTP 方法 "GET"(默认) / "POST"（POST+JSON 接口用）
        json: POST 请求体（method="POST" 时生效；网关按 method+body 转发并进 cache key）
    """
    h = dict(headers or {})
    if tier:
        h[_TIER_HEADER] = tier

    if _GW:
        if method.upper() == "POST" and json is not None:
            # POST：?u=url（params 仍随 query 转发），body 放请求体经网关透传
            return requests.post(
                _GW,
                params={"u": url, **(params or {})},
                json=json,
                headers=h,
                timeout=timeout,
                **kwargs,
            )
        # GET：u=原始URL，其余参数转发；tier 在头里
        return requests.get(
            _GW,
            params={"u": url, **(params or {})},
            headers=h,
            timeout=timeout,
            **kwargs,
        )
    # 家庭 IP 不可快速更换：没有任何风控源直连逃生开关，网关故障时失败关闭。
    raise RuntimeError(
        "ASGK_GW 未设置：风控源（东财/同花顺）禁止直连。"
        "请配置网关地址：设环境变量 ASGK_GW=http://localhost:7700，"
        "或用 ASGK_ENV 指向包含 ASGK_GW=... 的 .env 文件。"
    )
