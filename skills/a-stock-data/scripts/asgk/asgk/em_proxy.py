"""asgk.em_proxy — 统一请求入口 em_get（网关）+ _server_call（能力代理服务端）。

两个出网通道（能力代理重构 §3.3/§3.4 渐进迁移）：

1. em_get(url, ...) — 旧路径，透明 HTTP 代理（sgw），风控源必经。
   设了 ASGK_GW → 转发到网关（全局限流+缓存）；没设 → 失败关闭（禁止直连）。
   未下沉到能力代理服务端的函数仍走这条。

2. _server_call(capability, params) — 新路径，调能力代理服务端的语义接口。
   设了 ASGK_SERVER → POST /v1/<capability>，返回结构化数据；没设 → 返回 None
   （调用方据此回退旧 em_get 路径，保证未部署服务端时不 break）。
   已下沉的函数（如 tencent_quote）优先走这条。

ASGK_GW / ASGK_SERVER 的来源（优先级从高到低）：
    1. 环境变量（最高，部署时 systemd/container envfile 用这个）
    2. .env 文件（从 cwd 或 ASGK_ENV 指定路径加载）
环境变量优先于 .env。
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
_SERVER = os.environ.get("ASGK_SERVER")  # 如 http://127.0.0.1:7701；未设则回退旧路径
_TIER_HEADER = "X-Cache-Tier"


def _server_call(capability: str, params: dict, timeout: int = 15):
    """调能力代理服务端的语义接口：POST /v1/<capability>。

    返回结构化数据（dict/list），或 None（未配服务端 / 调用失败 / 服务端报错）。
    调用方据此回退旧 em_get 路径（§3.4 渐进迁移：未部署服务端时不 break）。

    与 em_get 的区别：em_get 返回 requests.Response（原始字节，调用方自解析）；
    _server_call 返回已解析的结构化数据（服务端持有全部上游知识）。
    """
    if not _SERVER:
        return None
    try:
        r = requests.post(
            f"{_SERVER}/v1/{capability}", json=params, timeout=timeout,
        )
    except requests.RequestException:
        return None  # 服务端未启动/不可达 → 回退
    if r.status_code != 200:
        return None  # 服务端报错（熔断/上游失败）→ 回退
    try:
        payload = r.json()
    except ValueError:
        return None
    return payload.get("data")


def em_get(url: str, params: dict | None = None, headers: dict | None = None,
           timeout: int = 15, tier: str | None = None, *,
           method: str = "GET", json: dict | None = None,
           data: dict | None = None, **kwargs) -> requests.Response:
    """统一请求入口。

    Args:
        url: 上游完整 URL
        params: query 参数
        headers: 请求头
        timeout: 超时秒
        tier: 缓存档位 P/L/S/R/N（仅走网关时生效）；None 则网关走兜底规则
        method: HTTP 方法 "GET"(默认) / "POST"
        json: POST JSON 请求体（method="POST" 时；网关按 method+body 转发并进 cache key）
        data: POST form 请求体（method="POST" 时，form-encoded；优先级低于 json）
    """
    h = dict(headers or {})
    if tier:
        h[_TIER_HEADER] = tier

    if _GW:
        if method.upper() == "POST":
            if json is not None:
                # POST+JSON：?u=url，body 放请求体经网关透传
                return requests.post(
                    _GW, params={"u": url, **(params or {})},
                    json=json, headers=h, timeout=timeout, **kwargs,
                )
            if data is not None:
                # POST+form：?u=url，form body 放请求体，标明 form 类型让网关按 form 转发
                h["Content-Type"] = "application/x-www-form-urlencoded"
                return requests.post(
                    _GW, params={"u": url, **(params or {})},
                    data=data, headers=h, timeout=timeout, **kwargs,
                )
            # POST 空 body（如互动易第二步，参数全在 query）：仅 method=POST 无 body
            h["Content-Type"] = "application/x-www-form-urlencoded"
            return requests.post(
                _GW, params={"u": url, **(params or {})},
                data={}, headers=h, timeout=timeout, **kwargs,
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
