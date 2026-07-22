"""asgk — A股数据共享库（本项目产物）。

底层入口 em_get（走网关/直连自适应）+ 各层取数函数（P1 逐层移植）。
"""
from asgk.em_proxy import em_get
from asgk.reports import eastmoney_reports, eastmoney_industry_reports
from asgk.signal import (
    ths_hot_reason, hsgt_realtime, eastmoney_concept_blocks,
    eastmoney_fund_flow_minute, dragon_tiger_board, lockup_expiry,
    industry_comparison, daily_dragon_tiger,
)
from asgk.capital import (
    margin_trading, block_trade, holder_num_change,
    dividend_history, stock_fund_flow_120d,
)
from asgk.news import eastmoney_stock_news, cls_telegraph, eastmoney_global_news
from asgk.client import tdx_client
from asgk.base import mootdx_finance, mootdx_f10, eastmoney_stock_info, sina_financial_report

__all__ = [
    "em_get",
    "eastmoney_reports", "eastmoney_industry_reports",
    "ths_hot_reason", "hsgt_realtime", "eastmoney_concept_blocks",
    "eastmoney_fund_flow_minute", "dragon_tiger_board", "lockup_expiry",
    "industry_comparison", "daily_dragon_tiger",
    "margin_trading", "block_trade", "holder_num_change",
    "dividend_history", "stock_fund_flow_120d",
    "eastmoney_stock_news", "cls_telegraph", "eastmoney_global_news",
    "tdx_client",
    "mootdx_finance", "mootdx_f10", "eastmoney_stock_info", "sina_financial_report",
]
