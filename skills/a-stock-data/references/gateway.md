# 能力代理服务端接入

asgk CLI 不直连任何数据源——所有取数经**能力代理服务端**（asgk-server）出网。
服务端持有全部上游知识（URL/编码/字段映射/签名/协议），做全局限流+缓存+熔断；
CLI 只发语义请求（如「要 600519 实时行情」），零上游知识。

## 架构

```
asgk CLI (×1000)                      外网
   │  POST /v1/<capability>            │
   └─► localhost:7701 (asgk-server) ──►  eastmoney / tencent / sina / cninfo / ...
            │  全局令牌桶限流(按源分组)
            │  结构化缓存(六类数据型 + 文档型 LRU)
            │  同请求合并(singleflight)
            │  403/429 立即熔断 + 安全闩
            │  持有全部上游知识(URL/编码/字段映射/签名/协议)
```

## CLI 如何找到服务端

优先级（从高到低）：

1. 环境变量 `export ASGK_SERVER=http://127.0.0.1:7701`（最高，systemd/container envfile 用这个）
2. `~/.config/asgk/cli.toml`（service 脚本 `install` 时自动生成）
3. 包内默认 `cli.toml.default`（url = http://127.0.0.1:7701）

未配置或服务端不可达时，CLI 直接报错。

## 安装

```bash
# 推荐：随服务端一起装（CLI 自动配置好指向服务端）
./packages/asgk-server/scripts/asgk-server-service.sh install

# 或只装 CLI（服务端已在别处部署）
uv tool install packages/asgk-server
```

装出两个 bin：`asgk-server`（服务）+ `asgk`（CLI）。

## 服务端能力接口

`POST /v1/<capability>`，body 是参数 JSON。21 个具名能力：

| 能力 | 域 | 说明 |
|------|-----|------|
| `quote` | 行情 | 腾讯实时行情 |
| `baidu_kline` | 行情 | 百度带 MA 日 K |
| `mootdx` | 行情 | 通达信 TCP（bars/quotes/transaction/finance/f10） |
| `stock_info` | 基础 | 东财个股基本面 |
| `concept_blocks` | 信号 | 个股板块/概念归属 |
| `datacenter` | 数据中心 | 东财数据中心（15 函数共用） |
| `limitup_pool` | 打板 | 涨停/炸板/跌停/昨涨停四池 |
| `fund_flow` | 资金 | 资金流（分钟 + 120 日） |
| `holders` | 股东 | 十大股东 + 十大流通股东 |
| `reports` | 研报 | 东财研报 + 行业研报 |
| `clist` | 信号 | 行业排名 + 板块成分 |
| `news` | 新闻 | 个股新闻 + 全球资讯 |
| `em_hot` | 舆情 | 人气榜 + 热门概念 |
| `ths_signal` | 信号 | 同花顺热榜 + 热点原因 + 北向 |
| `sina_option` | 期权 | ETF 期权 codes/tquote/greeks |
| `sina_finance` | 财报 | 新浪财报三表 |
| `cninfo` | 公告 | 巨潮公告 + 互动易 |
| `cls_telegraph` | 新闻 | 财联社电报 |
| `legulegu` | 估值 | 全市场 PE/PB 历史 |
| `chip` | 筹码 | 筹码分布 |
| `docs` | 文档 | 公告/研报 PDF 原文下载 |

CLI 的 9 大类 × 子命令是这 21 能力的细粒度映射（见 `asgk --list`）。

## 缓存档位（服务端侧）

| 档位 | 默认策略 | 典型数据 |
|------|----------|----------|
| P | 30 天 | 研报、公告、分红、F10 等发布后定稿数据 |
| L | 1 天 | 财报三表、股东户数等季度数据 |
| S | 盘中不缓存，盘后约 12 小时 | 龙虎榜、融资融券、板块等日级数据 |
| R | 不做跨时刻缓存 | 行情、K 线、资金流等实时数据 |
| N | 不缓存 | 新闻、电报等流式数据 |

缓存档位由服务端能力声明（`@capability(cache_policy=...)`），CLI 侧无需关心。

## 并发与故障保护（服务端侧）

asgk-server 提供：

- 按来源组的全局限流和随机抖动；东财建议不超过 1 req/s。
- 相同请求的 single-flight 合并，避免并发缓存未命中形成流量尖峰。
- 403/429 触发来源级熔断；冷却期只读缓存，恢复时只放行一个 canary。
- 403/429 立即熔断（家庭 IP 无法快速更换）。

主源被封的降级策略见 [failover](failover.md)。
