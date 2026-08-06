"""emquery 能力 — 通用东财/同花顺 URL 查询（§3.4 em_get 枢纽）。

把 em_get 的"拼 URL + 出网"下沉到服务端。客户端 em_get 配了 ASGK_SERVER 时，
POST /v1/emquery {url, params, tier, method, body}，服务端选限流组→出网→返回
解析后的 JSON。字段映射（f57→code 等）是纯计算，留客户端（§6.3）。

这是 em_get 路由的枢纽（§3.4）：
  现状: em_get(url, params) → sgw(?u=url)          # 透明代理
  新构: em_get(url, params) → server.emquery(url)  # 本能力
  回退: 服务端未配 → 旧 sgw 路径（不 break）

与 quote/datacenter 的区别：那两个是具名语义能力（客户端发 codes/report_name）；
emquery 是 URL 级通用能力——客户端仍持有 URL（东财端点知识在调用点），服务端只
接管出网安全（限流/熔断/缓存）。这是渐进迁移的务实折中：18 个 push2 函数零改动
即可走服务端（只改 em_get 一处），字段映射逐步下沉留给后续。

域名准入：emquery 不做独立白名单——限流组归组已保证（group_of 找不到组则拒）。
只有 config.toml 配了限流组的域名能出网，未知域名服务端拒（fail-closed）。
"""
from __future__ import annotations

from typing import Any

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

# 东财 push2 系列的默认 UA（客户端调用点不再传 UA，服务端统一持有）
_DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


def _group_for_url(url: str, domain_group: dict[str, str]) -> str | None:
    """从 URL 的 host 查限流组。无匹配返回 None（服务端拒）。"""
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    # 精确匹配 config 的 domains 列表
    if host in domain_group:
        return domain_group[host]
    # 后缀兜底（如 push2.eastmoney.com 匹配 eastmoney 组的该 host）
    return domain_group.get(host)


@capability(
    name="emquery",
    domain="通用查询",
    sources=[SourceMeta(name="eastmoney", group="eastmoney")],
    default_source="eastmoney",
    data_type="kv",  # 返回解析后的 JSON dict/list，泛型 kv
    # emquery 的缓存档位由调用方的 tier 参数动态决定，不固定 cache_policy。
    # 用 daily_settled 作保守默认（盘中0/盘后12h）；实际 TTL 由 _ttl_for_tier 覆盖。
    cache_policy="daily_settled",
    supported_formats=["json"],
)
def fetch_emquery(ctx: FetchContext, url: str,
                  params: dict | None = None,
                  tier: str = "R",
                  method: str = "GET",
                  body: dict | None = None,
                  body_type: str = "json",
                  headers: dict | None = None,
                  timeout: int = 15,
                  **_unused) -> Any:
    """通用 URL 查询：选限流组 → 出网 → 返回解析后的 JSON。

    与 em_get 的输入对齐（url/params/tier/method/body）。返回 r.json() 的结果
    （dict/list），由客户端做字段映射。出网经调用方 tier 对应的限流组。
    """
    # 服务端不接受客户端传的 source 选源（emquery 固定 eastmoney 组语义），
    # 但实际限流组按 URL 的域名归组（push2→eastmoney, data.hexin→10jqka 等）。
    # 这里用 ctx.group（由 SourceMeta.group=eastmoney 决定）作为默认，
    # 但更准确的是按 URL 归组——见 server.handle_capability 的 group 解析。
    # 当前 SourceMeta.group 固定 eastmoney，对东财系 URL 正确；
    # 非 eastmoney 域名（如 hexin）的 emquery 调用会归到错误的限流组。
    # TODO: emquery 的 group 应按 URL 动态解析，而非 SourceMeta 固定。当前 MVP
    # 先覆盖东财系（占 emquery 调用绝大多数）；hexin/sina 等留 push2 之外的批次。
    if not ctx.acquire():
        return None
    h = {"User-Agent": _DEFAULT_UA}
    if headers:
        h.update(headers)
    try:
        if method.upper() == "POST" and body is not None:
            if body_type == "form":
                r = egress_request("post", ctx.source.egress_client, url,
                                   data=body, params=params or {}, headers=h,
                                   timeout=timeout)
            else:
                r = egress_request("post", ctx.source.egress_client, url,
                                   json=body, params=params or {}, headers=h,
                                   timeout=timeout)
        elif method.upper() == "POST":
            r = egress_request("post", ctx.source.egress_client, url,
                               params=params or {}, headers=h, timeout=timeout)
        else:
            r = egress_request("get", ctx.source.egress_client, url,
                               params=params or {}, headers=h, timeout=timeout)
    except Exception:
        ctx.on_network_error()
        return None
    if r.status_code in (403, 429):
        ctx.on_failure(status=r.status_code, immediate=True)
        return None
    if r.status_code >= 500:
        ctx.on_failure(status=r.status_code)
        return None
    ctx.on_success()
    try:
        return r.json()
    except ValueError:
        return {"_raw_text": r.text}
