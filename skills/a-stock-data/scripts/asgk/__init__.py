"""asgk — A股数据共享库（本项目产物）。

底层入口 em_get（走网关/直连自适应）+ 各层取数函数（P1 逐层移植）。
"""
from asgk.em_proxy import em_get
from asgk.reports import eastmoney_reports, eastmoney_industry_reports

__all__ = ["em_get", "eastmoney_reports", "eastmoney_industry_reports"]
