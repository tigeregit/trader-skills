"""asgk_server.egress — 出网客户端选择（从 sgw/proxy.py._egress_request 搬入）。

按 source 的 egress_client 选择出网方式：
  - requests（默认）：标准 requests，适用于绝大多数源
  - curl_cffi：带 Chrome TLS 指纹(impersonate=chrome)，用于有协议栈风控的源(百度)

从 sgw 改造的点：sgw 的 _egress_request 是 Gateway 方法、接收 EndpointPolicy；
能力代理改为独立函数、接收 client 名字符串（"requests"/"curl_cffi"），由能力的
source 元数据决定。其余（.get/.post 分发、timeout 默认、impersonate）一致。
"""
from __future__ import annotations

import requests


def egress_request(method: str, client: str, url: str, **kwargs) -> "requests.Response":
    """按 client 选出网客户端。

    - requests(默认)：标准 requests，适用于绝大多数源。用 .get/.post
      （非 .request）以保持与 sgw 一致的出网语义。
    - curl_cffi：带 Chrome TLS 指纹(impersonate=chrome)，用于有协议栈风控的源(百度)。
      curl_cffi 的 RequestsError 继承 requests.RequestException，异常处理兼容。
    """
    kwargs.setdefault("timeout", 15)
    if client == "curl_cffi":
        from curl_cffi import requests as curl_requests
        kwargs["impersonate"] = "chrome"
        return curl_requests.request(method, url, **kwargs)
    # requests 路径用具名方法（get/post），便于测试 mock 且语义清晰
    fn = requests.get if method == "get" else requests.post
    return fn(url, **kwargs)
