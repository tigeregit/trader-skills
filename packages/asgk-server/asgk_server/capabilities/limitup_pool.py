"""limitup_pool 能力 — 东财涨停/炸板/跌停/昨涨停四池（push2ex）。

把 asgk/limitup.py 的 _em_zt_api 上游知识（push2ex URL、ut 常量、dpt 参数、
pool 数组解析）下沉到服务端。四个池（zt/zb/dt/yzt）共用同一查询机制，仅
endpoint + sort 不同——用 pool_type 参数区分，一个能力覆盖四池。

客户端 em_zt_pool/em_zb_pool/em_dt_pool/em_yzt_pool 发 {pool_type, date}，
服务端选 endpoint→出网→返回 result.data.pool 原始记录。字段映射（c→code、
p/1000→price、zttj→zt_stat 等）是纯计算，留客户端（§6.3）。

与 asgk/limitup.py._em_zt_api 的契约一致：
  输入：pool_type（zt/zb/dt/yzt）、date（YYYYMMDD）
  输出：pool 数组（每只一条原始 dict），空则 []
"""
from __future__ import annotations

from typing import Any

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

# 四池的端点 + sort（从 asgk/limitup.py._em_zt_api + 各函数调用点提取）
_POOLS: dict[str, dict[str, str]] = {
    "zt":  {"endpoint": "getTopicZTPool", "sort": "fbt:asc"},
    "zb":  {"endpoint": "getTopicZBPool", "sort": "fbt:asc"},
    "dt":  {"endpoint": "getTopicDTPool", "sort": "fund:asc"},
    "yzt": {"endpoint": "getYesterdayZTPool", "sort": "zs:desc"},
}
_POOL_BASE = "https://push2ex.eastmoney.com/"
_UT = "7eea3edcaed734bea9cbfc24409ed989"  # 东财涨停池固定凭据（非动态签名）
_REFERER = "https://quote.eastmoney.com/"


@capability(
    name="limitup_pool",
    domain="打板",
    sources=[SourceMeta(name="eastmoney", group="eastmoney")],
    default_source="eastmoney",
    data_type="table",
    cache_policy="realtime",  # 盘中实时变（涨停池秒级变），no-cache + singleflight 合并
    supported_formats=["json", "csv", "md", "xlsx"],
)
def fetch_limitup_pool(ctx: FetchContext, pool_type: str, date: str,
                       **_unused) -> list[dict]:
    """东财涨停板四池查询。pool_type ∈ {zt, zb, dt, yzt}，date=YYYYMMDD。

    返回 result.data.pool 原始记录数组（空则 []，data 为 null = 非交易日）。
    """
    spec = _POOLS.get(pool_type)
    if spec is None:
        return []  # 未知 pool_type → 空（服务端不报错，客户端拿到 [] 自行处理）
    url = _POOL_BASE + spec["endpoint"]
    params = {"ut": _UT, "dpt": "wz.ztzt", "Pageindex": 0,
              "pagesize": 10000, "sort": spec["sort"], "date": date}
    if not ctx.acquire():
        return None  # type: ignore[return-value]
    try:
        r = egress_request("get", ctx.source.egress_client, url, params=params,
                           headers={"Referer": _REFERER}, timeout=10)
    except Exception:
        ctx.on_network_error()
        return None  # type: ignore[return-value]
    if r.status_code in (403, 429):
        ctx.on_failure(status=r.status_code, immediate=True)
        return None  # type: ignore[return-value]
    if r.status_code >= 500:
        ctx.on_failure(status=r.status_code)
        return None  # type: ignore[return-value]
    ctx.on_success()
    return (r.json().get("data") or {}).get("pool") or []
