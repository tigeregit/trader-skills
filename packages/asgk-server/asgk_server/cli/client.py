"""asgk_server.cli.client — 纯 HTTP 客户端：POST /v1/<capability>。

CLI 不依赖任何业务函数库——直接把语义参数 POST 给服务端，服务端持有全部上游
知识出网（限流/缓存/熔断），返回结构化数据或 base64 编码的文档 bytes。

与旧客户端 ``em_proxy._server_call`` 的区别：
  - 无 legacy 回退（旧路径 em_get/ASGK_GW 已删除，sgw DEPRECATED）。
  - 失败直接抛异常（旧版返回 None 触发回退），错误信息更清晰。
  - 文档型（doc）：服务端返回 base64 bytes，本模块解码回原始 bytes。
"""
from __future__ import annotations

import base64
from typing import Any

import requests

from .config import resolve_server


class ServerError(RuntimeError):
    """服务端返回非 200 或调用失败。"""


def call(capability: str, params: dict, *,
         server: str | None = None, timeout: int = 15) -> Any:
    """调服务端结构化能力：POST /v1/<capability>。

    Args:
        capability: 服务端能力名（quote/mootdx/datacenter/...）。
        params: 语义参数 JSON（如 {"codes": ["600519"]}）。
        server: 服务端地址（None 则 resolve_server()）。
        timeout: 超时秒。
    Returns:
        服务端返回的结构化数据（dict/list）。
    Raises:
        ServerError: 服务端不可达 / 返回非 200 / JSON 解析失败。
    """
    base = (server or resolve_server())
    url = f"{base}/v1/{capability}"
    try:
        r = requests.post(url, json=params, timeout=timeout)
    except requests.RequestException as e:
        raise ServerError(f"服务端不可达 {url}: {e}") from e
    if r.status_code != 200:
        # 服务端报错（未知能力/熔断/上游失败），把 body 带出来便于排查
        body = r.text[:300] if r.text else "(空)"
        raise ServerError(f"服务端返回 {r.status_code}: {body}")
    try:
        payload = r.json()
    except ValueError as e:
        raise ServerError(f"服务端返回非 JSON: {r.text[:300]}") from e
    return payload.get("data")


def call_binary(capability: str, params: dict, *,
                server: str | None = None, timeout: int = 120) -> tuple[bytes, str]:
    """调服务端文档型能力：POST /v1/<capability>，返回 (bytes, ext)。

    文档型（doc）服务端返回 {"data": {"b64": "...", "ext": "pdf"}, ...}，
    本函数解码 b64 拿回原始 bytes。

    Args:
        capability: 服务端能力名（docs）。
        params: 语义参数（如 {"doc_type": "announce_pdf", "anno_id": ..., "code": ...}）。
    Returns:
        (data_bytes, ext)。
    Raises:
        ServerError: 服务端不可达/返回非 200/无数据。
    """
    base = (server or resolve_server())
    url = f"{base}/v1/{capability}"
    try:
        r = requests.post(url, json=params, timeout=timeout)
    except requests.RequestException as e:
        raise ServerError(f"服务端不可达 {url}: {e}") from e
    if r.status_code != 200:
        body = r.text[:300] if r.text else "(空)"
        raise ServerError(f"服务端返回 {r.status_code}: {body}")
    try:
        payload = r.json()
    except ValueError as e:
        raise ServerError(f"服务端返回非 JSON: {r.text[:300]}") from e
    data = payload.get("data")
    if not data or "b64" not in data:
        raise ServerError("服务端未返回文档数据（anno_id/info_code 无对应文档）")
    raw = base64.b64decode(data["b64"])
    ext = data.get("ext", "pdf")
    return raw, ext


def query_sources(server: str | None = None,
                  capability: str | None = None) -> Any:
    """查服务端 GET /v1/sources 列出能力支持的源。

    Args:
        capability: 指定时只返回该能力的源列表；None 返回全部 {cap: [sources]}。
    """
    base = (server or resolve_server())
    url = f"{base}/v1/sources"
    params = {"capability": capability} if capability else None
    try:
        r = requests.get(url, params=params, timeout=10)
    except requests.RequestException as e:
        raise ServerError(f"服务端不可达 {url}: {e}") from e
    if r.status_code != 200:
        raise ServerError(f"服务端返回 {r.status_code}")
    return r.json()
