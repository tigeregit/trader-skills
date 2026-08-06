"""baidu_kline 能力 — 百度股市通 K线（自带 MA，curl_cffi 指纹出网）。

把 asgk/quote.py.baidu_kline_with_ma 的上游知识下沉到服务端：
  - URL: https://finance.pae.baidu.com/selfselect/getstockquotation
  - params（group=quotation_kline_ab, newFormat=1, ktype=1 日K, ...）
  - headers（Accept/vnd.finance-web + Origin/Referer gushitong）
  - **curl_cffi 出网**：百度按协议栈画像区分请求，普通 urllib/requests 即使带完整
    Chrome headers 仍返回 ResultCode=403；source 的 egress_client=curl_cffi 指定。
  - **ResultCode 风控判定**：{"ResultCode":"0", "Result":{...newMarketData...}} 才
    正常；ResultCode=403 或非 0 = 风控/异常。服务端判定异常→反馈熔断→返回 None。

客户端发 {code, start_time}，服务端返回 {keys:[...], rows:[CSV串,...]} 或 None。
MA 字段表（ma5/ma10/ma20）就是 keys 数组里的英文列名，客户端按下标对齐即可，
无字段映射需下沉（这是「series」型数据的契约：原样返回 keys+rows）。

注意：百度风控敏感，curl_cffi 指纹未必长期可用；上游返回非 0 ResultCode 时
服务端按熔断处理（on_failure），客户端拿到 None 走回退（mootdx_bars 等日K源）。
"""
from __future__ import annotations

from typing import Any

from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

_URL = "https://finance.pae.baidu.com/selfselect/getstockquotation"
_REFERER = "https://gushitong.baidu.com/"


@capability(
    name="baidu_kline",
    domain="行情",
    sources=[SourceMeta(name="baidu", group="baidu", egress_client="curl_cffi")],
    default_source="baidu",
    data_type="series",  # keys + rows 原样返回，客户端按下标对齐
    cache_policy="daily_settled",  # 日K盘后定稿（含今日实时根 R，但日级粒度足够）
    supported_formats=["json", "csv"],
)
def fetch_baidu_kline(ctx: FetchContext, code: str, start_time: str = "",
                      **_unused) -> dict[str, Any] | None:
    """百度带 MA 的日 K 线。

    返回 {keys:[英文字段名...], rows:[CSV串,...]}；风控/异常返回 None
    （服务端已反馈熔断，客户端走回退路径）。
    """
    params = {"all": "1", "isIndex": "false", "isBk": "false", "isBlock": "false",
              "isFutures": "false", "isStock": "true", "newFormat": "1",
              "group": "quotation_kline_ab", "finClientType": "pc",
              "code": code, "start_time": start_time, "ktype": "1"}
    headers = {
        "Accept": "application/vnd.finance-web.v1+json",
        "Origin": _REFERER.rstrip("/"),
        "Referer": _REFERER,
    }
    if not ctx.acquire():
        return None
    try:
        r = egress_request("get", ctx.source.egress_client, _URL, params=params,
                           headers=headers, timeout=10)
    except Exception:
        ctx.on_network_error()
        return None
    if r.status_code in (403, 429):
        ctx.on_failure(status=r.status_code, immediate=True)
        return None
    if r.status_code >= 500:
        ctx.on_failure(status=r.status_code)
        return None

    # ResultCode 风控判定（HTTP 200 也可能业务层拒绝）
    try:
        d = r.json()
    except ValueError:
        ctx.on_failure(status=r.status_code)  # 非 JSON = 上游异常
        return None
    if not isinstance(d, dict):
        ctx.on_failure(status=r.status_code)
        return None
    code_str = str(d.get("ResultCode", ""))
    result = d.get("Result")
    # 正常：ResultCode=="0" 且 Result 是 dict。否则按上游异常反馈熔断。
    if r.status_code != 200 or code_str != "0" or not isinstance(result, dict):
        # ResultCode=403 是百度业务层风控（与 HTTP 403 同义），按 immediate 熔断
        immediate = (code_str == "403" or r.status_code == 403)
        ctx.on_failure(status=r.status_code, immediate=immediate)
        return None
    ctx.on_success()
    md = result.get("newMarketData", {}) or {}
    return {"keys": md.get("keys", []) or [],
            "rows": (md.get("marketData", "") or "").split(";")}
