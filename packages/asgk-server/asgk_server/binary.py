"""asgk_server.binary — 文档型能力的二进制载荷标记。

文档能力（announce_pdf / report_pdf）返回原始 bytes，无法经 JSON-RPC
（{"data": bytes}）传输，也不能走结构化缓存（存 dict/list）。

BinaryPayload 是一个标记对象：docs 能力返回它，server 检测到后：
  1. 不走结构化缓存（SemanticCache），改走 DocumentCache（bytes 文件 + LRU）
  2. HTTP 响应 base64 编码：{"data": "<b64>", "_binary": true, "ext": ..., "content_type": ...}

客户端 _server_call_binary 解码 base64 拿回 bytes。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BinaryPayload:
    """文档能力的返回标记：原始 bytes + 文件扩展名 + MIME。

    data: 文档原始 bytes（PDF/xlsx/...）
    ext: 文件扩展名（pdf/xlsx/...），用于 DocumentCache 存盘 + 客户端写文件
    content_type: MIME 类型（application/pdf 等），HTTP 响应头用
    """

    data: bytes
    ext: str
    content_type: str = "application/octet-stream"
