"""asgk — A股数据共享库（本项目产物）。

P0 阶段只提供统一请求入口 em_get（走网关/直连自适应）。
各层取数函数（quote/reports/...）在 P1（scripts-library-port）移植。
"""
from asgk.em_proxy import em_get

__all__ = ["em_get"]
