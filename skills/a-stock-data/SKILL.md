---
name: a-stock-data
description: 当任务需要获取A股真实数据时使用——行情(K线/五档/PE/PB/市值)、研报(评级/一致预期EPS)、信号(热点/北向/龙虎榜/解禁/行业)、资金面(融资融券/大宗/股东户数/分红/资金流)、业绩(预告/快报)、事件(高管增减持/回购/机构调研)、新闻(财联社电报/全球资讯)、财务三表/F10、公告、打板(涨停池/炸板率)、ETF期权(希腊字母/IV)、舆情(互动易/热榜)、公告/研报 PDF 原文下载等。提供自包含 Python 库 asgk，并通过部署方提供的能力代理服务端（asgk-server）支持单 IP 下多 agent 并发，持有全部上游知识（URL/编码/字段映射/签名），客户端只发语义请求。仅在需要取数时使用，概念讨论/投资观点无需加载。
---

# A股数据 skill

提供 A 股取数能力，**不定义交易策略**。经**能力代理服务端**（asgk-server）出网——服务端持有全部上游知识（URL/编码/字段映射/签名/协议），做全局限流+缓存+熔断；客户端只发语义请求（如「要 600519 实时行情」），零上游知识。

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
| 深交所融资融券(官方容灾) | `margin_detail_szse` | capital |
| 股东户数/分红 | `holder_num_change`/`dividend_history` | capital |
| 资金流 | `stock_fund_flow_120d` | capital |
| 业绩预告/快报 | `earning_forecast`/`earning_express` | [earning](references/earning.md) |
| 高管增减持/回购/机构调研 | `mgmt_trade`/`repurchase`/`institute_research` | [risk_event](references/risk_event.md) |
| 股权质押/商誉 | `pledge_ratio`/`goodwill` | [pool_filter](references/pool_filter.md) |
| 十大股东/流通股东/股东变化/协同 | `top10_holders`/`top10_free_holders`/`holder_change`/`holder_teamwork` | [holders](references/holders.md) |
| 板块成份股 | `board_constituents` | [board](references/board.md) |
| 筹码分布/主力成本 | `chip_distribution` | [chip](references/chip.md) |
| 全市场PE/PB历史 | `market_pe_lg`/`market_pb_lg` | [valuation_hist](references/valuation_hist.md) |
| 财联社电报/新闻 | `cls_telegraph`/`eastmoney_stock_news` | [news](references/news.md) |
| 财务三表 | `sina_financial_report` | [base](references/base.md) |
| F10/股本/上市日 | `mootdx_f10`/`eastmoney_stock_info` | base |
| 公告 | `cninfo_announcements` | [announce](references/announce.md) |
| 公告/研报 PDF 原文 | `announce_pdf`/`report_pdf` | announce |
| 涨停池/炸板率 | `em_zt_pool`/`limit_up_sentiment` | [limitup](references/limitup.md) |
| ETF期权/希腊字母 | `sina_option_greeks` | [option](references/option.md) |
| 互动易/热榜 | `cninfo_irm`/`ths_hot_list` | [sentiment](references/sentiment.md) |
| 估值(PE/PEG) | `full_valuation`/`calc_peg` | [valuation](references/valuation.md) |

**需要某层详细字段/示例时，读对应 reference 文件（按需加载，不必全读）。**

## 使用方式

先把 `A_STOCK_SKILL_DIR` 设为本 `SKILL.md` 所在目录的绝对路径；之后可从任意
工作目录安装和调用自带的 asgk：

```bash
A_STOCK_SKILL_DIR=/absolute/path/to/a-stock-data
export ASGK_SERVER=http://127.0.0.1:7701   # 能力代理服务端
uv sync --no-dev --project "$A_STOCK_SKILL_DIR/scripts"
uv run --no-dev --project "$A_STOCK_SKILL_DIR/scripts" python -c "
from asgk import tencent_quote, eastmoney_reports, full_valuation

q = tencent_quote(['600519'])           # PE/PB/市值（经服务端 quote 能力）
reports = eastmoney_reports('600519')    # 研报（经服务端 reports 能力）
v = full_valuation('600519')             # 完整估值（纯计算，不下沉）
"
```

也可用 CLI（自动发现 `@source(cli=...)` 声明的函数为子命令）：

```bash
uv run --no-dev --project "$A_STOCK_SKILL_DIR/scripts" python -m asgk quote 600519 --format md
uv run --no-dev --project "$A_STOCK_SKILL_DIR/scripts" python -m asgk announce_pdf 1225431263 600519 --output file --path anno.pdf
```

### 环境配置

业务函数内部按「能力代理优先」路由（§3.4 渐进迁移）：

1. **优先**：`ASGK_SERVER` → 调能力代理服务端 `POST /v1/<capability>`，服务端持有
   全部上游知识出网（限流+缓存+熔断）。
2. **回退**：服务端未配/不可达/报错 → 回退旧 `em_get` 路径，需 `ASGK_GW`（sgw 网关，
   已 DEPRECATED，保留作旧路径回退）。

```bash
# 推荐：指向能力代理服务端（新架构主路径）
export ASGK_SERVER=http://127.0.0.1:7701

# 旧路径回退（仅当服务端未部署时需要；sgw 已 DEPRECATED）
export ASGK_GW=http://127.0.0.1:7700
```

也可把 `ASGK_SERVER=...` / `ASGK_GW=...` 写入任意 `.env`，再用 `ASGK_ENV` 指向该文件。
风控源（东财/同花顺）不存在直连 fallback——未配置或无法连接服务端/网关时应失败关闭。
完整接入协议、缓存档位和部署边界见 [gateway](references/gateway.md)。


## 数据源 & 能力代理

- **能力代理服务端**（asgk-server）：单进程，吞噬全部流量内核（令牌桶限流/熔断/
  缓存/singleflight），按数据域暴露 21 个具名能力（quote/kline/announce/...）。
  服务端持有全部上游知识，客户端零上游知识。
- **限流分组**：按源分组（eastmoney ≤1 req/s、tencent、sina、cls、cninfo、baidu、
  mootdx、legulegu），跨进程全局生效——无论多少 agent 并发，外网出口收敛。
- 服务端是部署环境为 100~1000 agent 共享的外部运行服务，不从本 skill 内启动。
  部署见 `packages/asgk-server/README.md`。
- 主源被封的降级策略见 [failover](references/failover.md)。

## 安装

```bash
A_STOCK_SKILL_DIR=/absolute/path/to/a-stock-data
uv sync --no-dev --project "$A_STOCK_SKILL_DIR/scripts"
```

## 已知限制

- 业务函数经能力代理服务端取数（ASGK_SERVER）；服务端未部署/不可达时回退旧
  `em_get` 路径（ASGK_GW/sgw，已 DEPRECATED）。两条路径返回结构一致（零破坏）。
- `mootdx_bars` 在 mootdx 0.11.7 返回空日 K 时自动降级到百度；非日线频率不做
  非等价降级。
- mootdx 需国内网络（TCP 7709 海外超时）。
- 北向深股通(sgt)自2024-08披露收紧，仅参考。
