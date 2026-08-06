---
name: a-stock-data
description: 当任务需要获取A股真实数据时使用——行情(K线/五档/PE/PB/市值)、研报(评级/一致预期EPS/估值/PEG)、信号(热点/北向/龙虎榜/行业/板块/筹码)、资金面(融资融券/大宗/股东户数/分红/资金流/十大股东)、业绩(预告/快报)、事件(高管增减持/回购/机构调研/解禁/互动易)、风控(股权质押/商誉/涨停池/炸板率)、新闻(财联社电报/全球资讯/热榜/公告)、衍生(ETF期权希腊字母/公告研报PDF原文)等。提供 asgk CLI（9 大类 × 子命令），经部署方的能力代理服务端（asgk-server）出网，服务端持有全部上游知识（URL/编码/字段映射/签名），做全局限流+缓存+熔断，CLI 只发语义请求。仅在需要取数时使用，概念讨论/投资观点无需加载。
---

# A股数据 skill

提供 A 股取数能力，**不定义交易策略**。经**能力代理服务端**（asgk-server）出网——
服务端持有全部上游知识（URL/编码/字段映射/签名/协议），做全局限流+缓存+熔断；
CLI 只发语义请求（如「要 600519 实时行情」），零上游知识。

## 前置：安装

skill 只含文档，取数靠 `asgk` CLI + `asgk-server` 服务端（同一包装两个 bin）。
安装、启动服务、systemd/background 后端选择、单例防多开机制等详见
[安装与启动](references/install.md)。

最简流程（clone 全仓后）：

```bash
./packages/asgk-server/scripts/asgk-server-service.sh install
# 装出 asgk-server + asgk 两个 bin，自动启动服务（systemd 或后台），配置好 CLI
asgk --list                  # 验证：列出 9 大类 × 子命令
asgk quote realtime 600519   # 验证：茅台实时行情
```

## 快速决策：要什么数据？

命令结构：`asgk <大类> <子命令> [参数] [--format json|csv|md|xlsx]`

| 需求 | 命令 | 参考 |
|------|------|------|
| **quote · 行情** | | |
| 实时价/PE/PB/市值 | `asgk quote realtime 600519` | [quote](references/quote.md) |
| 日K线(带均线) | `asgk quote kline 600519` | quote |
| 通达信日K | `asgk quote bars 600519` | quote |
| 五档盘口 | `asgk quote quotes 600519` | quote |
| 逐笔成交 | `asgk quote tick 600519` | quote |
| **base · 基本面** | | |
| 财务三表 | `asgk base report 600519` | [base](references/base.md) |
| F10/公司概况 | `asgk base f10 600519` | base |
| 个股基本面 | `asgk base info 600519` | base |
| 通达信财务 | `asgk base finance 600519` | base |
| 业绩预告/快报 | `asgk base forecast 2024-12-31` | [earning](references/earning.md) |
| 全市场PE/PB历史 | `asgk base pe_hist` / `pb_hist` | [valuation_hist](references/valuation_hist.md) |
| **report · 研报** | | |
| 个股研报/评级 | `asgk report list 600519` | [report](references/report.md) |
| 行业研报 | `asgk report industry` | report |
| 一致预期EPS | `asgk report eps 600519` | report |
| 完整估值 | `asgk report valuation 600519` | [valuation](references/valuation.md) |
| PEG/远期PE(纯计算) | `asgk report peg 25 0.2` | valuation |
| **flow · 资金** | | |
| 120日资金流 | `asgk flow fundflow 600519` | [capital](references/capital.md) |
| 融资融券 | `asgk flow margin 600519` | capital |
| 大宗交易 | `asgk flow block 600519` | capital |
| 股东户数/分红 | `asgk flow holders_n 600519` / `dividend 600519` | capital |
| 十大股东/流通股东 | `asgk flow top10 600519 2024-09-30` | [holders](references/holders.md) |
| **signal · 信号** | | |
| 当日强势股/题材 | `asgk signal hot` | [signal](references/signal.md) |
| 龙虎榜 | `asgk signal dragon 600519 2026-08-05` | signal |
| 个股板块归属 | `asgk signal block 600519` | signal |
| 行业排名 | `asgk signal industry` | signal |
| 北向资金 | `asgk signal north` | signal |
| 板块成份股 | `asgk signal board_c BK0475` | [board](references/board.md) |
| 筹码分布/主力成本 | `asgk signal chip 600519` | [chip](references/chip.md) |
| **event · 事件** | | |
| 高管增减持/回购/调研 | `asgk event mgmt` / `repo` / `research 2026-07-01` | [risk_event](references/risk_event.md) |
| 解禁 | `asgk event lockup 600519 2026-08-05` | risk_event |
| 互动易 | `asgk event irm 600519` | [sentiment](references/sentiment.md) |
| **risk · 风控** | | |
| 股权质押/商誉 | `asgk risk pledge 2026-08-05` / `goodwill 2026-08-05` | [pool_filter](references/pool_filter.md) |
| 涨停池/炸板池 | `asgk risk zt 2026-08-05` / `zb 2026-08-05` | [limitup](references/limitup.md) |
| 打板情绪 | `asgk risk sentiment 2026-08-05` | limitup |
| **news · 资讯** | | |
| 财联社电报 | `asgk news telegraph` | [news](references/news.md) |
| 个股新闻 | `asgk news stock 600519` | news |
| 同花顺热榜/东财人气榜 | `asgk news hot_list` / `rank` | [sentiment](references/sentiment.md) |
| 公告 | `asgk news announce 600519` | [announce](references/announce.md) |
| **deriv · 衍生** | | |
| ETF期权/希腊字母 | `asgk deriv opt_greek OP10004257` | [option](references/option.md) |
| 公告PDF原文 | `asgk deriv announce_pdf <anno_id> 600519 --output file --path a.pdf` | announce |
| 研报PDF原文 | `asgk deriv report_pdf <info_code> --output file --path r.pdf` | [docs](references/docs.md) |

**查看某层详细字段/示例时，读对应 reference 文件（按需加载，不必全读）。**
**用 `asgk --list` 看全部 64 个子命令的完整参数签名。**

## 使用方式

```bash
# 默认输出 markdown 表格（table/kv/series 型）
asgk quote realtime 600519

# 切换格式
asgk quote realtime 600519 --format json    # JSON
asgk flow fundflow 600519 --format csv     # CSV
asgk base report 600519 --format xlsx --output file --path report.xlsx  # 写 Excel

# 多值参数（codes 型）
asgk quote realtime 600519 000858 600809    # 三只股票同时查

# 文档型（PDF）必须 --output file
asgk deriv announce_pdf 1225431263 600519 --output file --path anno.pdf

# 查某能力支持的数据源
asgk quote realtime --sources

# 纯计算（不调服务端）
asgk report peg 25 0.2                       # PEG = PE/(CAGR*100)
asgk report digest 40 0.15 --target-pe 25    # PE消化到25x需几年
```

## 数据源 & 能力代理

- **能力代理服务端**（asgk-server）：单进程，吞噬全部流量内核（令牌桶限流/熔断/
  缓存/singleflight），按数据域暴露 21 个具名能力。服务端持有全部上游知识，
  CLI 零上游知识。
- **限流分组**：按源分组（eastmoney ≤1 req/s、tencent、sina、cls、cninfo、baidu、
  mootdx、legulegu），跨进程全局生效——无论多少 agent 并发，外网出口收敛。
- 服务端是部署环境为 100~1000 agent 共享的外部运行服务，不从本 skill 内启动。
  安装/启动/systemd/background 后端见 [install](references/install.md)。
- 主源被封的降级策略见 [failover](references/failover.md)。
- 服务端能力接口与地址配置见 [gateway](references/gateway.md)。

## 已知限制

- CLI 经能力代理服务端取数（`ASGK_SERVER`）。服务端未部署/不可达时直接报错
  （**不再回退旧 sgw 路径**——sgw 已 DEPRECATED，legacy 回退已移除）。
- `asgk quote bars`（mootdx）在 0.11.7 返回空日 K 时由服务端自动降级到百度；非日线
  频率不做非等价降级。
- mootdx 需国内网络（TCP 7709 海外超时）。
- 北向深股通(sgt)自2024-08披露收紧，仅参考。
- `--format xlsx` 需额外依赖：`uv tool install "asgk-server[xlsx]"`（装 pandas/openpyxl）。
