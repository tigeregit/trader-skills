---
name: a-stock-data
description: 当任务需要获取A股真实数据时使用——行情(K线/五档/PE/PB/市值)、研报(评级/一致预期EPS)、信号(热点/北向/龙虎榜/解禁/行业)、资金面(融资融券/大宗/股东户数/分红/资金流)、业绩(预告/快报)、事件(高管增减持/回购/机构调研)、新闻(财联社电报/全球资讯)、财务三表/F10、公告、打板(涨停池/炸板率)、ETF期权(希腊字母/IV)、舆情(互动易/热榜)等。提供 Python 库(asgk) + CLI + 共享流量网关，支持单 IP 下多 agent 并发。仅在需要取数时使用，概念讨论/投资观点无需加载。
---

# A股数据 skill

本项目提供 A 股取数能力，**不定义交易策略**。所有东财/同花顺请求经共享网关（全局限流+缓存），腾讯/百度/新浪/mootdx 直连。

## 快速决策：要什么数据？

| 需求 | 函数 | 参考 |
|------|------|------|
| 实时价/PE/PB/市值 | `tencent_quote` | [quote](references/quote.md) |
| 日K线(带均线) | `baidu_kline_with_ma` | quote |
| 五档盘口/逐笔 | `mootdx_quotes`/`mootdx_transaction` | quote |
| 研报/评级 | `eastmoney_reports` | [report](references/report.md) |
| 一致预期EPS | `ths_eps_forecast` | report |
| 当日强势股/题材 | `ths_hot_reason` | [signal](references/signal.md) |
| 个股板块归属 | `eastmoney_concept_blocks` | signal |
| 龙虎榜 | `dragon_tiger_board`/`daily_dragon_tiger` | signal |
| 行业排名 | `industry_comparison` | signal |
| 融资融券/大宗 | `margin_trading`/`block_trade` | [capital](references/capital.md) |
| 股东户数/分红 | `holder_num_change`/`dividend_history` | capital |
| 资金流 | `stock_fund_flow_120d` | capital |
| 业绩预告/快报 | `earning_forecast`/`earning_express` | [earning](references/earning.md) |
| 高管增减持/回购/机构调研 | `mgmt_trade`/`repurchase`/`institute_research` | [risk_event](references/risk_event.md) |
| 股权质押/商誉 | `pledge_ratio`/`goodwill` | [pool_filter](references/pool_filter.md) |
| 十大股东/流通股东/股东变化/协同 | `top10_holders`/`top10_free_holders`/`holder_change`/`holder_teamwork` | [holders](references/holders.md) |
| 板块成份股 | `board_constituents` | [board](references/board.md) |
| 筹码分布/主力成本 | `chip_distribution` | [chip](references/chip.md) |
| 财联社电报/新闻 | `cls_telegraph`/`eastmoney_stock_news` | [news](references/news.md) |
| 财务三表 | `sina_financial_report` | [base](references/base.md) |
| F10/股本/上市日 | `mootdx_f10`/`eastmoney_stock_info` | base |
| 公告 | `cninfo_announcements` | [announce](references/announce.md) |
| 涨停池/炸板率 | `em_zt_pool`/`limit_up_sentiment` | [limitup](references/limitup.md) |
| ETF期权/希腊字母 | `sina_option_greeks` | [option](references/option.md) |
| 互动易/热榜 | `cninfo_irm`/`ths_hot_list` | [sentiment](references/sentiment.md) |
| 估值(PE/PEG) | `full_valuation`/`calc_peg` | [valuation](references/valuation.md) |

**需要某层详细字段/示例时，读对应 reference 文件（按需加载，不必全读）。**

## 使用方式

asgk 是 uv 项目，执行代码时须在 `scripts/` 目录（或用 `--project`）：

```bash
cd skills/a-stock-data/scripts && uv run python -c "
from asgk import tencent_quote, eastmoney_reports, full_valuation

q = tencent_quote(['600519'])           # PE/PB/市值（直连腾讯）
reports = eastmoney_reports('600519')    # 研报（经网关）
v = full_valuation('600519')             # 完整估值
"
```

### 环境配置

风控源（东财/同花顺）**必须经网关**，未配 `ASGK_GW` 调用会抛异常（禁止直连，防封 IP）。

```bash
# 1. 启网关（东财/同花顺限流+缓存）
cd packages/sgw && uv run sgw-proxy
#   生产环境指定指纹日志目录（按天自动拆分 sgw_fp_YYYYMMDD.jsonl）：
#   uv run sgw-proxy --fp-dir /var/log/sgw

# 2. 配置 ASGK_GW（二选一）
#    方式A（推荐，多 agent 部署）：环境变量（systemd/container envfile）
export ASGK_GW=http://localhost:7700
#    方式B（开发）：项目根 .env 文件（asgk 自动加载，子 agent 也能继承）
echo 'ASGK_GW=http://127.0.0.1:7700' > skills/a-stock-data/scripts/.env
```

`ASGK_GW` 来源优先级：环境变量 > .env。两者都未设时，em_get 抛异常。
仅调试时设 `ASGK_ALLOW_DIRECT=1` 可临时允许直连（不推荐）。


## 数据源优先级 & 网关

- **不封 IP 的源直连**：腾讯/百度/新浪/mootdx（TCP），不经网关。
- **有风控的源经网关**：东财(12子域)/同花顺(4子域)，全局限流 ≤1 req/s + 缓存。
- 网关是 100~1000 agent 并发的核心设施（单 IP 不封）。
- 主源被封的降级策略见 [failover](references/failover.md)。

## 缓存档位（由 @source 声明）

| 档 | TTL | 数据类型 |
|----|-----|---------|
| P | 30天 | 研报/公告/分红/F10（发布即定稿） |
| L | 1天 | 财报三表/股东户数（季度） |
| S | 盘后12h/盘中0 | 龙虎榜/融资融券/板块（日级定稿） |
| R | no-cache | 行情/K线/资金流（实时） |
| N | no-cache | 新闻电报（流式） |

详细缓存/分档机制见 `.agents/notes/gateway-design.md`。

## 安装

```bash
cd skills/a-stock-data/scripts
uv sync          # 安装 sgw + asgk 两个包
uv run sgw-proxy # 启网关
```

## 已知限制

- `mootdx_bars` 在 mootdx 0.11.7 返回空（日K用百度K线替代）。
- mootdx 需国内网络（TCP 7709 海外超时）。
- 北向深股通(sgt)自2024-08披露收紧，仅参考。
