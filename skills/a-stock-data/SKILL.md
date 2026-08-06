---
name: a-stock-data
description: 当任务需要获取A股真实数据时使用——行情(K线/五档/PE/PB/市值)、研报(评级/一致预期EPS/估值/PEG)、信号(热点/北向/龙虎榜/行业/板块/筹码)、资金面(融资融券/大宗/股东户数/分红/资金流/十大股东)、业绩(预告/快报)、事件(高管增减持/回购/机构调研/解禁/互动易)、风控(股权质押/商誉/涨停池/炸板率)、新闻(财联社电报/全球资讯/热榜/公告)、衍生(ETF期权希腊字母/公告研报PDF原文)等。提供 asgk CLI（9 大类 × 子命令），经部署方的能力代理服务端（asgk-server）出网，服务端持有全部上游知识（URL/编码/字段映射/签名），做全局限流+缓存+熔断，CLI 只发语义请求。仅在需要取数时使用，概念讨论/投资观点无需加载。
---

# A股数据 skill

提供 A 股取数能力，**不定义交易策略**。经**能力代理服务端**（asgk-server）出网——
服务端持有全部上游知识（URL/编码/字段映射/签名/协议），做全局限流+缓存+熔断；
CLI 只发语义请求（如「要 600519 实时行情」），零上游知识。

## 前置：安装 CLI

CLI 随 `asgk-server` 包安装（一个包装出 `asgk-server` + `asgk` 两个二进制）：

```bash
# 方式1：随服务端一起装（推荐，CLI 自动配置好指向服务端）
./packages/asgk-server/scripts/asgk-server-service.sh install
# install 会自动生成 ~/.config/asgk/cli.toml 指向本地服务端

# 方式2：只装 CLI（服务端已在别处部署）
uv tool install packages/asgk-server

# 验证
asgk --list          # 列出全部 9 大类 × 子命令
asgk 行情 realtime 600519   # 茅台实时行情
```

### CLI 如何找到服务端

优先级（从高到低）：
1. 环境变量 `export ASGK_SERVER=http://127.0.0.1:7701`（最高）
2. `~/.config/asgk/cli.toml`（service 脚本 install 时自动生成）
3. 包内默认 `http://127.0.0.1:7701`

## 快速决策：要什么数据？

命令结构：`asgk <大类> <子命令> [参数] [--format json|csv|md|xlsx]`

| 需求 | 命令 | 参考 |
|------|------|------|
| **行情** | | |
| 实时价/PE/PB/市值 | `asgk 行情 realtime 600519` | [quote](references/quote.md) |
| 日K线(带均线) | `asgk 行情 kline 600519` | quote |
| 通达信日K | `asgk 行情 bars 600519` | quote |
| 五档盘口 | `asgk 行情 quotes 600519` | quote |
| 逐笔成交 | `asgk 行情 tick 600519` | quote |
| **基本面** | | |
| 财务三表 | `asgk 基本面 report 600519` | [base](references/base.md) |
| F10/公司概况 | `asgk 基本面 f10 600519` | base |
| 个股基本面 | `asgk 基本面 info 600519` | base |
| 通达信财务 | `asgk 基本面 finance 600519` | base |
| 业绩预告/快报 | `asgk 基本面 forecast 2024-12-31` | [earning](references/earning.md) |
| 全市场PE/PB历史 | `asgk 基本面 pe_hist` / `pb_hist` | [valuation_hist](references/valuation_hist.md) |
| **研报** | | |
| 个股研报/评级 | `asgk 研报 list 600519` | [report](references/report.md) |
| 行业研报 | `asgk 研报 industry` | report |
| 一致预期EPS | `asgk 研报 eps 600519` | report |
| 完整估值 | `asgk 研报 valuation 600519` | [valuation](references/valuation.md) |
| PEG/远期PE(纯计算) | `asgk 研报 peg 25 0.2` | valuation |
| **资金** | | |
| 120日资金流 | `asgk 资金 fundflow 600519` | [capital](references/capital.md) |
| 融资融券 | `asgk 资金 margin 600519` | capital |
| 大宗交易 | `asgk 资金 block 600519` | capital |
| 股东户数/分红 | `asgk 资金 holders_n 600519` / `dividend 600519` | capital |
| 十大股东/流通股东 | `asgk 资金 top10 600519 2024-09-30` | [holders](references/holders.md) |
| **信号** | | |
| 当日强势股/题材 | `asgk 信号 hot` | [signal](references/signal.md) |
| 龙虎榜 | `asgk 信号 dragon 600519 2026-08-05` | signal |
| 个股板块归属 | `asgk 信号 block 600519` | signal |
| 行业排名 | `asgk 信号 industry` | signal |
| 北向资金 | `asgk 信号 north` | signal |
| 板块成份股 | `asgk 信号 board_c BK0475` | [board](references/board.md) |
| 筹码分布/主力成本 | `asgk 信号 chip 600519` | [chip](references/chip.md) |
| **事件** | | |
| 高管增减持/回购/调研 | `asgk 事件 mgmt` / `repo` / `research 2026-07-01` | [risk_event](references/risk_event.md) |
| 解禁 | `asgk 事件 lockup 600519 2026-08-05` | risk_event |
| 互动易 | `asgk 事件 irm 600519` | [sentiment](references/sentiment.md) |
| **风控** | | |
| 股权质押/商誉 | `asgk 风控 pledge 2026-08-05` / `goodwill 2026-08-05` | [pool_filter](references/pool_filter.md) |
| 涨停池/炸板池 | `asgk 风控 zt 2026-08-05` / `zb 2026-08-05` | [limitup](references/limitup.md) |
| 打板情绪 | `asgk 风控 sentiment 2026-08-05` | limitup |
| **资讯** | | |
| 财联社电报 | `asgk 资讯 telegraph` | [news](references/news.md) |
| 个股新闻 | `asgk 资讯 stock 600519` | news |
| 同花顺热榜/东财人气榜 | `asgk 资讯 hot_list` / `rank` | [sentiment](references/sentiment.md) |
| 公告 | `asgk 资讯 announce 600519` | [announce](references/announce.md) |
| **衍生** | | |
| ETF期权/希腊字母 | `asgk 衍生 opt_greek OP10004257` | [option](references/option.md) |
| 公告PDF原文 | `asgk 衍生 announce_pdf <anno_id> 600519 --output file --path a.pdf` | announce |
| 研报PDF原文 | `asgk 衍生 report_pdf <info_code> --output file --path r.pdf` | [docs](references/docs.md) |

**查看某层详细字段/示例时，读对应 reference 文件（按需加载，不必全读）。**
**用 `asgk --list` 看全部 64 个子命令的完整参数签名。**

## 使用方式

```bash
# 默认输出 markdown 表格（table/kv/series 型）
asgk 行情 realtime 600519

# 切换格式
asgk 行情 realtime 600519 --format json    # JSON
asgk 资金 fundflow 600519 --format csv     # CSV
asgk 基本面 report 600519 --format xlsx --output file --path report.xlsx  # 写 Excel

# 多值参数（codes 型）
asgk 行情 realtime 600519 000858 600809    # 三只股票同时查

# 文档型（PDF）必须 --output file
asgk 衍生 announce_pdf 1225431263 600519 --output file --path anno.pdf

# 查某能力支持的数据源
asgk 行情 realtime --sources

# 纯计算（不调服务端）
asgk 研报 peg 25 0.2                       # PEG = PE/(CAGR*100)
asgk 研报 digest 40 0.15 --target-pe 25    # PE消化到25x需几年
```

## 数据源 & 能力代理

- **能力代理服务端**（asgk-server）：单进程，吞噬全部流量内核（令牌桶限流/熔断/
  缓存/singleflight），按数据域暴露 21 个具名能力。服务端持有全部上游知识，
  CLI 零上游知识。
- **限流分组**：按源分组（eastmoney ≤1 req/s、tencent、sina、cls、cninfo、baidu、
  mootdx、legulegu），跨进程全局生效——无论多少 agent 并发，外网出口收敛。
- 服务端是部署环境为 100~1000 agent 共享的外部运行服务，不从本 skill 内启动。
  部署见 `packages/asgk-server/README.md`。
- 主源被封的降级策略见 [failover](references/failover.md)。
- 服务端地址配置见 [gateway](references/gateway.md)。

## 已知限制

- CLI 经能力代理服务端取数（`ASGK_SERVER`）。服务端未部署/不可达时直接报错
  （**不再回退旧 sgw 路径**——sgw 已 DEPRECATED，legacy 回退已移除）。
- `asgk 行情 bars`（mootdx）在 0.11.7 返回空日 K 时由服务端自动降级到百度；非日线
  频率不做非等价降级。
- mootdx 需国内网络（TCP 7709 海外超时）。
- 北向深股通(sgt)自2024-08披露收紧，仅参考。
- `--format xlsx` 需额外依赖：`uv tool install "asgk-server[xlsx]"`（装 pandas/openpyxl）。
