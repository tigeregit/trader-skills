"""asgk_server — 能力代理服务端。

吞噬 sgw 的流量内核（限流/缓存/熔断/singleflight），按数据域暴露语义能力
（quote/kline/announce/...）。客户端发语义请求，本服务作为 single user 出网。
"""
