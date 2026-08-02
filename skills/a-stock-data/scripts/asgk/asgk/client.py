"""asgk.client — mootdx 通达信 TCP 客户端封装。

通达信 TCP(7709) 不封 IP，但 mootdx 0.11.x 可能出现 BESTIP.HQ 空串，需显式
服务器探测兜底。K 线、五档盘口和逐笔成交共用此客户端。
"""
from __future__ import annotations

import socket

from mootdx.quotes import Quotes

# 2026-06 实测可用的通达信服务器，按延迟排序
_TDX_SERVERS = [
    ("119.97.185.59", 7709), ("124.70.133.119", 7709), ("116.205.183.150", 7709),
    ("123.60.73.44", 7709), ("116.205.163.254", 7709), ("121.36.225.169", 7709),
    ("123.60.70.228", 7709), ("124.71.9.153", 7709), ("110.41.147.114", 7709),
    ("124.71.187.122", 7709),
]


def _probe(ip: str, port: int, timeout: float = 2.0) -> bool:
    """TCP 握手探测，判断服务器是否可达。"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def tdx_client(market: str = "std"):
    """创建 mootdx 客户端，规避 0.11.x BESTIP.HQ 空串 bug。

    顺序兜底，保证 IP 列表老化/换网时仍能工作：
      1) 顺序探测 _TDX_SERVERS，用第一个 TCP 可达的显式 server；
      2) 全部不可达 → 回退 mootdx 自带 bestip 测速选优；
      3) 再不行 → 回退裸 factory；
      4) 仍失败 → 抛 RuntimeError。

    海外网络通常全部超时（TCP 7709），需走国内代理或更新服务器列表。
    """
    for ip, port in _TDX_SERVERS:
        if _probe(ip, port):
            return Quotes.factory(market=market, server=(ip, port))
    try:
        return Quotes.factory(market=market, bestip=True)
    except Exception:
        pass
    try:
        return Quotes.factory(market=market)
    except Exception as e:
        raise RuntimeError(
            "所有 mootdx 服务器均不可达。海外网络通常全部超时（TCP 7709），"
            "请走国内代理或更新 _TDX_SERVERS 列表。原始错误：%s" % e
        )
