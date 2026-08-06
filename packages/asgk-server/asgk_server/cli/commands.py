"""asgk_server.cli.commands — 9 大类 × 子命令映射表。

这是 CLI 的数据基石：每个子命令声明它调哪个服务端能力、传什么参数、数据形态。

设计要点：
  - 服务端能力是粗粒度（如 ``mootdx`` 含 bars/quotes/transaction/finance/f10 共 5 种），
    CLI 要细粒度子命令，必须有一份「子命令 → 能力 + 固定参数」的映射。
  - 映射放 CLI 侧（而非服务端 CapabilityMeta），因为这是**用户交互层**的关切，
    不应污染服务端能力注册表。
  - ``args`` 描述位置参数如何绑定到能力的语义参数（如 code/codes/date）。
  - ``data_type`` 驱动默认输出格式（table/kv/series→md，其余→json）。
  - ``local`` 标记的子命令不调服务端，纯本地计算（如 PEG）。

参数绑定规则（见 cli/__init__.py main()）：
  - ``args`` 中 is_list=True 的：收集多值位置参数（如 codes）。
  - ``args`` 中有 default 的：暴露为 --flag。
  - 其余：必填位置参数。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ArgSpec:
    """一个命令行位置/--flag 参数的声明。

    name:       能力侧的语义参数名（如 code/codes/date/page_size）。
    cli_name:   CLI 侧显示名（默认同 name；中文 help 用 desc）。
    desc:       中文说明（help 文本）。
    required:   是否必填（无 default 时 True）。
    default:    默认值（有则暴露为 --flag）。
    is_list:    是否多值收集（codes 型）。
    type:       值类型转换（str 默认 / float / int）。数字参数必须声明，
                否则 argparse 传字符串导致 local 计算或服务端解析失败。
    """

    name: str
    cli_name: str | None = None
    desc: str = ""
    required: bool = True
    default: object = None
    is_list: bool = False
    type: type = str
    positional: bool = False  # True=可选位置参数(nargs="?")，False且非required=--flag


@dataclass
class CmdSpec:
    """一个子命令的完整声明。

    category:   大类名（英文 token：quote/base/report/flow/signal/event/risk/news/deriv）。
    name:       子命令名（realtime/kline/bars/...）。
    capability: 服务端能力名（quote/mootdx/legulegu/...）；local=True 时可为空。
    fixed:      固定参数（随能力调用一并传，如 {"mootdx_type": "bars"}）。
    args:       位置/--flag 参数声明列表（绑定到能力的语义参数）。
    data_type:  数据形态（kv/table/series/text/doc），驱动默认格式。
    help:       中文说明。
    local:      是否纯本地计算（不调服务端）。local 命令在 cli/local.py 实现。
    local_fn:   local 命令的执行函数名（cli/local.py 中的符号）。
    """

    category: str
    name: str
    capability: str
    help: str
    data_type: str = "table"
    fixed: dict = field(default_factory=dict)
    args: list[ArgSpec] = field(default_factory=list)
    local: bool = False
    local_fn: str = ""
    orchestrator: str = ""  # 编排型命令标记（如 time_status：先调服务端再合并本地）


# ── 位置参数的常用别名（简化声明）──
def _code(desc: str = "6位股票代码") -> ArgSpec:
    return ArgSpec(name="code", desc=desc)


def _codes(desc: str = "6位股票代码（多值）") -> ArgSpec:
    return ArgSpec(name="codes", desc=desc, is_list=True)


def _date(desc: str = "日期 YYYY-MM-DD") -> ArgSpec:
    return ArgSpec(name="date", desc=desc)


def _opt(name: str, desc: str, default: object, *, type: type = str) -> ArgSpec:
    """可选 --flag 参数。"""
    return ArgSpec(name=name, desc=desc, required=False, default=default, type=type)


def _opt_pos(name: str, desc: str, default: object = None, *, type: type = str) -> ArgSpec:
    """可选位置参数（nargs="?"），如 trade_day 的 date（asgk time trade_day [DATE]）。"""
    return ArgSpec(name=name, desc=desc, required=False, default=default,
                   type=type, positional=True)


def _num(name: str, desc: str, *, type: type = float) -> ArgSpec:
    """必填数字型位置参数（用于纯计算命令）。"""
    return ArgSpec(name=name, desc=desc, type=type)


# ════════════════════════════════════════════════════════════════
# 9 大类 × 子命令映射表
# ════════════════════════════════════════════════════════════════
COMMANDS: list[CmdSpec] = [
    # ── 1. quote 行情（5）─────────────────────────────────────────────
    CmdSpec("quote", "realtime", "quote", "实时行情（PE/PB/市值/五档）",
            data_type="kv", args=[_codes()]),
    CmdSpec("quote", "kline", "baidu_kline", "日K线（带MA5/10/20）",
            data_type="series",
            args=[_code(), _opt("start_time", "起始日 YYYYMMDD", "")]),
    CmdSpec("quote", "bars", "mootdx", "通达信日K（mootdx TCP）",
            data_type="series",
            fixed={"mootdx_type": "bars"},
            args=[_code(), _opt("frequency", "K线周期(9=日)", 9, type=int),
                  _opt("offset", "拉取条数", 100, type=int)]),
    CmdSpec("quote", "quotes", "mootdx", "五档盘口",
            data_type="table",
            fixed={"mootdx_type": "quotes"},
            args=[ArgSpec(name="symbols", desc="代码列表（多值）", is_list=True)]),
    CmdSpec("quote", "tick", "mootdx", "逐笔成交",
            data_type="table",
            fixed={"mootdx_type": "transaction"},
            args=[_code(), _opt("date", "日期(空=当日)", None)]),

    # ── 2. base 基本面（8）───────────────────────────────────────────
    CmdSpec("base", "finance", "mootdx", "通达信财务数据",
            data_type="kv",
            fixed={"mootdx_type": "finance"},
            args=[_code()]),
    CmdSpec("base", "f10", "mootdx", "F10 公司概况",
            data_type="text",
            fixed={"mootdx_type": "f10"},
            args=[_code(), _opt("name", "F10栏目名", "公司概况")]),
    CmdSpec("base", "info", "stock_info", "东财个股基本面",
            data_type="kv", args=[_code()]),
    CmdSpec("base", "report", "sina_finance", "新浪财报三表",
            data_type="table",
            args=[_code(), _opt("report_type", "报表(lrb利润/zcfzb资产负债/xjll现金流)", "lrb"),
                  _opt("num", "拉取期数", 8, type=int)]),
    CmdSpec("base", "forecast", "datacenter", "业绩预告",
            data_type="table",
            fixed={"report_name": "RPT_LICO_FN_CPD"},
            args=[_date()]),
    CmdSpec("base", "express", "datacenter", "业绩快报",
            data_type="table",
            fixed={"report_name": "RPT_LICO_FN_CPD"},
            args=[_date()]),
    CmdSpec("base", "pe_hist", "legulegu", "全市场PE历史",
            data_type="table",
            fixed={"lg_type": "pe"},
            args=[_opt("market", "市场(上证/深证/创业板/...)", "上证")]),
    CmdSpec("base", "pb_hist", "legulegu", "全市场PB历史",
            data_type="table",
            fixed={"lg_type": "pb"},
            args=[_opt("market", "市场(上证/深证/创业板/...)", "上证")]),

    # ── 3. report 研报（7）─────────────────────────────────────────────
    CmdSpec("report", "list", "reports", "东财个股研报",
            data_type="table",
            fixed={"report_type": "stock"},
            args=[_code(), _opt("max_pages", "拉取页数", 5, type=int)]),
    CmdSpec("report", "industry", "reports", "行业研报",
            data_type="table",
            fixed={"report_type": "industry"},
            args=[_opt("industry_code", "行业代码(*=全部)", "*"),
                  _opt("max_pages", "拉取页数", 5, type=int),
                  _opt("begin", "起始日 YYYY-MM-DD", "2024-01-01")]),
    CmdSpec("report", "eps", "datacenter", "一致预期EPS（ Thompson/THS）",
            data_type="table",
            fixed={"report_name": "RPT_WEB_RESPREDICT_HS",
                   "dc_source": "WEB"},
            args=[_code()]),
    CmdSpec("report", "valuation", "quote", "完整估值快照（串联行情+EPS）",
            data_type="kv", args=[_code()]),
    # 3 个纯计算（local）
    CmdSpec("report", "fwd_pe", "", "远期PE = 股价/一致预期EPS（纯计算）",
            data_type="kv", local=True, local_fn="forward_pe",
            args=[_num("price", "当前股价"),
                  _num("eps_forecast", "一致预期EPS")]),
    CmdSpec("report", "digest", "", "PE消化年数（纯计算）",
            data_type="kv", local=True, local_fn="pe_digestion",
            args=[_num("current_pe", "当前PE"),
                  _num("cagr", "复合增长率(0.15=15%)"),
                  _opt("target_pe", "目标PE", 30, type=float)]),
    CmdSpec("report", "peg", "", "PEG = PE/(CAGR*100)（纯计算）",
            data_type="kv", local=True, local_fn="calc_peg",
            args=[_num("pe", "PE"),
                  _num("cagr", "复合增长率(0.15=15%)")]),

    # ── 4. flow 资金（11）────────────────────────────────────────────
    CmdSpec("flow", "fundflow", "fund_flow", "120日资金流",
            data_type="series",
            fixed={"period": "120d"},
            args=[_code()]),
    CmdSpec("flow", "flow_min", "fund_flow", "分钟资金流",
            data_type="series",
            fixed={"period": "minute"},
            args=[_code()]),
    CmdSpec("flow", "margin", "datacenter", "融资融券",
            data_type="table",
            fixed={"report_name": "RPTA_WEB_RZRQ_GGMX"},
            args=[_code(), _opt("page_size", "每页条数", 30, type=int)]),
    CmdSpec("flow", "margin_sz", "datacenter", "深交所融资融券明细",
            data_type="table",
            fixed={"report_name": "RPT_RTC_GGMX"},
            args=[_date()]),
    CmdSpec("flow", "block", "datacenter", "大宗交易",
            data_type="table",
            fixed={"report_name": "RPT_BLOCKTRADE_DETAIL"},
            args=[_code(), _opt("page_size", "每页条数", 20, type=int)]),
    CmdSpec("flow", "holders_n", "datacenter", "股东户数变化",
            data_type="table",
            fixed={"report_name": "RPT_LICO_SHCHOLDERNUM"},
            args=[_code(), _opt("page_size", "每页条数", 10, type=int)]),
    CmdSpec("flow", "dividend", "datacenter", "分红历史",
            data_type="table",
            fixed={"report_name": "RPT_SHAREBONUS_DIVIDEND"},
            args=[_code(), _opt("page_size", "每页条数", 20, type=int)]),
    CmdSpec("flow", "top10", "holders", "十大股东",
            data_type="table",
            fixed={"holder_type": "sdgd"},
            args=[_code(), _date()]),
    CmdSpec("flow", "top10_f", "holders", "十大流通股东",
            data_type="table",
            fixed={"holder_type": "ltgd"},
            args=[_code(), _date()]),
    CmdSpec("flow", "holder_c", "holders", "股东变化",
            data_type="table",
            fixed={"holder_type": "change"},
            args=[_date()]),
    CmdSpec("flow", "teamwork", "holders", "股东协同关系",
            data_type="table",
            fixed={"holder_type": "teamwork"},
            args=[_opt("date", "报告期(空=最新)", "")]),

    # ── 5. signal 信号（8）─────────────────────────────────────────────
    CmdSpec("signal", "hot", "ths_signal", "当日强势股/热点原因",
            data_type="table",
            fixed={"signal_type": "hot_reason"},
            args=[_opt("date", "日期(空=当日)", None)]),
    CmdSpec("signal", "dragon", "ths_signal", "龙虎榜（个股）",
            data_type="kv",
            fixed={"signal_type": "dragon_board"},
            args=[_code(), _date(),
                  _opt("look_back", "回看天数", 30, type=int)]),
    CmdSpec("signal", "dragon_d", "ths_signal", "龙虎榜（每日）",
            data_type="kv",
            fixed={"signal_type": "daily_dragon"},
            args=[_opt("date", "日期(空=当日)", None),
                  _opt("min_net_buy", "最小净买入(万元)", None, type=float)]),
    CmdSpec("signal", "block", "concept_blocks", "个股板块/概念归属",
            data_type="kv", args=[_code()]),
    CmdSpec("signal", "industry", "clist", "行业排名/对比",
            data_type="kv",
            fixed={"query_type": "industry_comparison"},
            args=[_opt("top_n", "前N", 20, type=int)]),
    CmdSpec("signal", "north", "datacenter", "北向资金实时",
            data_type="table",
            fixed={"report_name": "RPT_MUTUAL_DEAL_HISTORY"},
            args=[]),
    CmdSpec("signal", "board_c", "clist", "板块成份股",
            data_type="table",
            fixed={"query_type": "board_constituents"},
            args=[ArgSpec(name="symbol", desc="板块代码/名称"),
                  _opt("kind", "板块类型(concept/industry)", "concept")]),
    CmdSpec("signal", "chip", "chip", "筹码分布/主力成本",
            data_type="table", args=[_code(), _opt("adjust", "复权(q前/h后/空)", "")]),

    # ── 6. event 事件（5）─────────────────────────────────────────────
    CmdSpec("event", "mgmt", "datacenter", "高管增减持",
            data_type="table",
            fixed={"report_name": "RPT_MANAGERS_TRADE"},
            args=[]),
    CmdSpec("event", "repo", "datacenter", "回购",
            data_type="table",
            fixed={"report_name": "RPT_REPURCHASE"},
            args=[]),
    CmdSpec("event", "research", "datacenter", "机构调研",
            data_type="table",
            fixed={"report_name": "RPT_ORG_RESEARCH"},
            args=[ArgSpec(name="start_date", desc="起始日 YYYY-MM-DD")]),
    CmdSpec("event", "lockup", "datacenter", "解禁",
            data_type="table",
            fixed={"report_name": "RPT_LIFTUP_LIST"},
            args=[_code(), _date(),
                  _opt("forward_days", "前看天数", 90, type=int)]),
    CmdSpec("event", "irm", "cninfo", "互动易（投资者问答）",
            data_type="table",
            fixed={"cninfo_type": "irm"},
            args=[_code(), _opt("page_size", "每页条数", 30, type=int),
                  _opt("page_num", "页码", 1, type=int)]),

    # ── 7. risk 风控（8）─────────────────────────────────────────────
    CmdSpec("risk", "pledge", "datacenter", "股权质押比例",
            data_type="table",
            fixed={"report_name": "RPT_PLEDGE_RATIO"},
            args=[_date()]),
    CmdSpec("risk", "goodwill", "datacenter", "商誉",
            data_type="table",
            fixed={"report_name": "RPT_GOODWILL"},
            args=[_date()]),
    CmdSpec("risk", "zt", "limitup_pool", "涨停池",
            data_type="table",
            fixed={"pool_type": "zt"},
            args=[_date()]),
    CmdSpec("risk", "zb", "limitup_pool", "炸板池",
            data_type="table",
            fixed={"pool_type": "zb"},
            args=[_date()]),
    CmdSpec("risk", "dt", "limitup_pool", "跌停池",
            data_type="table",
            fixed={"pool_type": "dt"},
            args=[_date()]),
    CmdSpec("risk", "yzt", "limitup_pool", "昨涨停池",
            data_type="table",
            fixed={"pool_type": "yzt"},
            args=[_date()]),
    CmdSpec("risk", "ths_zt", "ths_signal", "同花顺涨停池",
            data_type="table",
            fixed={"signal_type": "limit_up_pool"},
            args=[_date()]),
    CmdSpec("risk", "sentiment", "ths_signal", "打板情绪（炸板率等）",
            data_type="kv",
            fixed={"signal_type": "limit_up_sentiment"},
            args=[_date()]),

    # ── 8. news 资讯（7）─────────────────────────────────────────────
    CmdSpec("news", "stock", "news", "个股新闻",
            data_type="table",
            fixed={"news_type": "stock"},
            args=[_code(), _opt("page_size", "每页条数", 20, type=int)]),
    CmdSpec("news", "telegraph", "cls_telegraph", "财联社电报",
            data_type="table",
            args=[_opt("page_size", "每页条数", 50, type=int)]),
    CmdSpec("news", "global", "news", "全球资讯",
            data_type="table",
            fixed={"news_type": "global"},
            args=[_opt("page_size", "每页条数", 50, type=int)]),
    CmdSpec("news", "hot_list", "ths_signal", "同花顺热榜",
            data_type="table",
            fixed={"signal_type": "hot_list"},
            args=[_opt("period", "周期(hour/day/week)", "hour")]),
    CmdSpec("news", "rank", "em_hot", "东财人气榜",
            data_type="table",
            fixed={"hot_type": "rank"},
            args=[_opt("top", "前N", 50, type=int)]),
    CmdSpec("news", "concept", "em_hot", "热门概念",
            data_type="table",
            fixed={"hot_type": "concept"},
            args=[_code()]),
    CmdSpec("news", "announce", "cninfo", "公告列表",
            data_type="table",
            fixed={"cninfo_type": "announce"},
            args=[_code(), _opt("page_size", "每页条数", 30, type=int)]),

    # ── 9. deriv 衍生（5）─────────────────────────────────────────────
    CmdSpec("deriv", "opt_codes", "sina_option", "期权合约列表",
            data_type="kv",
            fixed={"option_type": "codes"},
            args=[_opt("underlying", "标的代码", "510050"),
                  _opt("call", "认购(True)/认沽(False)", True)]),
    CmdSpec("deriv", "opt_quote", "sina_option", "期权报价",
            data_type="kv",
            fixed={"option_type": "tquote"},
            args=[ArgSpec(name="code", desc="期权合约代码")]),
    CmdSpec("deriv", "opt_greek", "sina_option", "期权希腊字母",
            data_type="kv",
            fixed={"option_type": "greeks"},
            args=[ArgSpec(name="code", desc="期权合约代码")]),
    CmdSpec("deriv", "announce_pdf", "docs", "公告PDF原文下载",
            data_type="doc",
            fixed={"doc_type": "announce_pdf"},
            args=[ArgSpec(name="anno_id", desc="公告ID(annoId)"),
                  _code()]),
    CmdSpec("deriv", "report_pdf", "docs", "研报PDF原文下载",
            data_type="doc",
            fixed={"doc_type": "report_pdf"},
            args=[ArgSpec(name="info_code", desc="研报infoCode")]),

    # ── 10. time 交易时序（4）──────────────────────────────────
    CmdSpec("time", "now", "", "当前日期时间（含星期/是否周末，纯本地）",
            data_type="kv", local=True, local_fn="time_now", args=[]),
    CmdSpec("time", "trade_day", "calendar", "判定日期是否交易日（经服务端交易日历）",
            data_type="kv",
            fixed={"calendar_type": "trade_day"},
            args=[_opt_pos("date", "日期 YYYY-MM-DD（空=今天）")]),
    CmdSpec("time", "trade_session", "", "当前是否交易时段（含盘前/午休，纯本地）",
            data_type="kv", local=True, local_fn="trade_session", args=[]),
    CmdSpec("time", "status", "", "合并：当前时间+交易时段+是否交易日",
            data_type="kv", local=True, local_fn="time_status", args=[],
            # 特殊标记：status 需先调服务端拿 trade_day，再合并本地。见 __init__.py
            orchestrator="time_status"),
]


# ── 索引辅助 ──────────────────────────────────────────────────
def by_category() -> dict[str, list[CmdSpec]]:
    """按大类分组（驱动 argparse 子命令树 + 帮助文本）。"""
    out: dict[str, list[CmdSpec]] = {}
    for cmd in COMMANDS:
        out.setdefault(cmd.category, []).append(cmd)
    return out


def find(category: str, name: str) -> CmdSpec | None:
    """按 大类+子命令名 精确查找。"""
    for cmd in COMMANDS:
        if cmd.category == category and cmd.name == name:
            return cmd
    return None
