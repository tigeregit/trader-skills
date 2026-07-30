"""asgk — A股数据共享库（本项目产物）。

底层入口 em_get（走网关/直连自适应）+ 各层取数函数。
移植自 ref/a-stock-data，按 asgk-contract.md 契约组织。
"""
from asgk.em_proxy import em_get
from asgk.quote import (
    mootdx_bars, mootdx_quotes, mootdx_transaction,
    tencent_quote, baidu_kline_with_ma,
)
from asgk.reports import eastmoney_reports, eastmoney_industry_reports, ths_eps_forecast
from asgk.signal import (
    ths_hot_reason, hsgt_realtime, eastmoney_concept_blocks,
    eastmoney_fund_flow_minute, dragon_tiger_board, lockup_expiry,
    industry_comparison, daily_dragon_tiger,
)
from asgk.capital import (
    margin_trading, block_trade, holder_num_change,
    dividend_history, stock_fund_flow_120d,
)
from asgk.earning import earning_forecast, earning_express
from asgk.risk_event import mgmt_trade, repurchase, institute_research
from asgk.pool_filter import pledge_ratio, goodwill
from asgk.holders import (
    top10_holders, top10_free_holders, holder_change, holder_teamwork,
)
from asgk.board import board_constituents
from asgk.chip import chip_distribution
from asgk.valuation_hist import market_pe_lg, market_pb_lg
from asgk.news import eastmoney_stock_news, cls_telegraph, eastmoney_global_news
from asgk.client import tdx_client
from asgk.base import mootdx_finance, mootdx_f10, eastmoney_stock_info, sina_financial_report
from asgk.announce import cninfo_announcements
from asgk.limitup import (
    em_zt_pool, em_zb_pool, em_dt_pool, em_yzt_pool,
    ths_limit_up_pool, limit_up_sentiment,
)
from asgk.option import sina_option_codes, sina_option_tquote, sina_option_greeks
from asgk.sentiment import cninfo_irm, ths_hot_list, em_hot_rank, em_hot_concept
from asgk.valuation import forward_pe, pe_digestion, calc_peg, full_valuation

__all__ = [
    # 底层
    "em_get",
    # 行情层
    "mootdx_bars", "mootdx_quotes", "mootdx_transaction",
    "tencent_quote", "baidu_kline_with_ma",
    # 研报层
    "eastmoney_reports", "eastmoney_industry_reports", "ths_eps_forecast",
    # 信号层
    "ths_hot_reason", "hsgt_realtime", "eastmoney_concept_blocks",
    "eastmoney_fund_flow_minute", "dragon_tiger_board", "lockup_expiry",
    "industry_comparison", "daily_dragon_tiger",
    # 资金面
    "margin_trading", "block_trade", "holder_num_change",
    "dividend_history", "stock_fund_flow_120d",
    # 业绩层
    "earning_forecast", "earning_express",
    # 事件层
    "mgmt_trade", "repurchase", "institute_research",
    # 风险/筛选层
    "pledge_ratio", "goodwill",
    # 股东层
    "top10_holders", "top10_free_holders", "holder_change", "holder_teamwork",
    # 板块层
    "board_constituents",
    # 筹码层
    "chip_distribution",
    # 估值历史层
    "market_pe_lg", "market_pb_lg",
    # 新闻层
    "eastmoney_stock_news", "cls_telegraph", "eastmoney_global_news",
    # 基础数据
    "tdx_client", "mootdx_finance", "mootdx_f10",
    "eastmoney_stock_info", "sina_financial_report",
    # 公告层
    "cninfo_announcements",
    # 打板层
    "em_zt_pool", "em_zb_pool", "em_dt_pool", "em_yzt_pool",
    "ths_limit_up_pool", "limit_up_sentiment",
    # 期权层
    "sina_option_codes", "sina_option_tquote", "sina_option_greeks",
    # 舆情层
    "cninfo_irm", "ths_hot_list", "em_hot_rank", "em_hot_concept",
    # 估值
    "forward_pe", "pe_digestion", "calc_peg", "full_valuation",
]
