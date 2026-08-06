"""fund_flow 能力 — 东财个股资金流（分钟级 + 120日级，push2his fflow 端点系）。

把 asgk/signal.py.eastmoney_fund_flow_minute 与 asgk/capital.py.stock_fund_flow_120d
的上游知识下沉到服务端。两函数共用 secid + klines CSV-split 解析，端点/字段集/参数
不同——用 period 参数区分（minute / daily120）。

客户端发 {code, period}，服务端构造 secid + 选端点 + 出网 + 解析 klines CSV →
返回结构化 [{time/date, main_net, small_net, mid_net, large_net, super_net}]。
CSV split + 字段索引下沉服务端，客户端零上游知识。

与客户端契约一致：
  minute  → push2/fflow/kline/get（klt=1，6 字段 f51-f57），返回 time 键
  daily120 → push2his/fflow/daykline/get（lmt=120，15 字段 f51-f65），返回 date 键
"""
from __future__ import annotations

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability
from .push2 import _egress_get, _secid

_PUSH2 = "https://push2.eastmoney.com/api/qt"
_PUSH2HIS = "https://push2his.eastmoney.com/api/qt"


@capability(
    name="fund_flow",
    domain="资金面",
    sources=[SourceMeta(name="eastmoney", group="eastmoney")],
    default_source="eastmoney",
    data_type="series",
    cache_policy="realtime",  # 资金流秒级变（minute）/ 日级（daily120 盘后定稿），
                              # 统一 realtime（no-cache + singleflight），避免盘中脏缓存
    supported_formats=["json", "csv", "md", "xlsx"],
)
def fetch_fund_flow(ctx: FetchContext, code: str, period: str = "minute",
                    **_unused) -> list[dict]:
    """东财个股资金流。period ∈ {minute, daily120}。

    minute:   分钟级资金流（盘中），push2 fflow/kline/get，klt=1
    daily120: 最近120交易日日级资金流，push2his fflow/daykline/get，lmt=120

    返回 [{time|date, main_net, small_net, mid_net, large_net, super_net}, ...]（元）。
    klines CSV split + 字段索引在服务端解析。
    """
    if period == "minute":
        data = _egress_get(ctx, f"{_PUSH2}/stock/fflow/kline/get", params={
            "secid": _secid(code), "klt": 1,
            "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56,f57",
        })
        if data is None:
            return None  # type: ignore[return-value]
        return _parse_klines(data, key="time", min_parts=6)

    if period == "daily120":
        data = _egress_get(ctx, f"{_PUSH2HIS}/stock/fflow/daykline/get", params={
            "secid": _secid(code),
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "lmt": "120",
        })
        if data is None:
            return None  # type: ignore[return-value]
        return _parse_klines(data, key="date", min_parts=7, dash_zero=True)

    return []  # 未知 period → 空


def _parse_klines(data: dict, *, key: str, min_parts: int,
                  dash_zero: bool = False) -> list[dict]:
    """解析东财 klines CSV 数组 → 结构化记录。

    每行 "time,main,small,mid,large,super[,...]"，split 后按位置取前 6 字段。
    key: time（minute）或 date（daily）。dash_zero: "-" 当 0 处理（日级有缺失值）。
    """
    rows: list[dict] = []
    for line in (data.get("data") or {}).get("klines", []):
        parts = line.split(",")
        if len(parts) < min_parts:
            continue

        def _val(idx: int) -> float:
            v = parts[idx]
            if dash_zero and v == "-":
                return 0
            return float(v)

        rows.append({
            key: parts[0],
            "main_net": _val(1), "small_net": _val(2), "mid_net": _val(3),
            "large_net": _val(4), "super_net": _val(5),
        })
    return rows
