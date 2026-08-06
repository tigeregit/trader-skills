# asgk-server — A股能力代理服务端 + asgk CLI

单进程 HTTP 服务，供单 IP 下 100~1000 个 agent 并发共享。取代已 DEPRECATED 的
`packages/sgw`（透明代理）：吞噬 sgw 全部流量内核（令牌桶限流 / 熔断 / 缓存 /
singleflight），并暴露**语义能力接口**（`POST /v1/<capability>`）。

## 包内容（一个包装两个 bin）

`uv tool install asgk-server` 装出两个二进制：

| bin | 作用 | 入口 |
|-----|------|------|
| `asgk-server` | 能力代理服务端（本服务） | `asgk_server.server:main` |
| `asgk` | 数据获取 CLI（9 大类 × 子命令） | `asgk_server.cli:main` |

CLI 是**纯 HTTP 客户端**，不依赖业务函数库，直接 POST 服务端 `/v1/<capability>`。
服务端地址解析：`ASGK_SERVER` 环境变量 > `~/.config/asgk/cli.toml` > 包内默认（7701）。

```bash
asgk --list                       # 列出全部 9 大类 × 子命令
asgk 行情 realtime 600519         # 茅台实时行情
asgk 研报 peg 25 0.2              # PEG 纯计算（不调服务端）
```

## 它做什么

```
agent (×1000)                         外网
   │  语义请求                          │
   └─► localhost:7701 (asgk-server) ──►  eastmoney / tencent / sina / cninfo / ...
            │  全局令牌桶限流(按源分组)
            │  结构化缓存(六类数据型 + 文档型 LRU)
            │  同请求合并(singleflight)
            │  403/429 立即熔断 + 安全闩
            │  持有全部上游知识(URL/编码/字段映射/签名/协议)
```

相对 sgw 的「透明代理」（转发 `?u=URL`，不懂数据语义），asgk-server 持有全部上游
知识，客户端只发语义请求（如「要 600519 实时行情」），**零上游知识**。

## 能力清单（21 个具名能力）

| 能力 | 域 | 源 | 说明 |
|------|-----|-----|------|
| `quote` | 行情 | tencent | 腾讯实时行情（GBK + 53 字段映射） |
| `baidu_kline` | 行情 | baidu | 百度带 MA 日 K（curl_cffi 指纹） |
| `mootdx` | 行情 | mootdx | 通达信 TCP（bars/quotes/transaction/finance/f10） |
| `stock_info` | 基础 | eastmoney | 东财个股基本面 |
| `concept_blocks` | 信号 | eastmoney | 个股板块/概念归属 |
| `datacenter` | 数据中心 | eastmoney | 东财数据中心（15 函数共用） |
| `limitup_pool` | 打板 | eastmoney | 涨停/炸板/跌停/昨涨停四池 |
| `fund_flow` | 资金 | eastmoney | 资金流（分钟 + 120 日） |
| `holders` | 股东 | eastmoney | 十大股东 + 十大流通股东 |
| `reports` | 研报 | eastmoney | 东财研报 + 行业研报 |
| `clist` | 信号 | eastmoney | 行业排名 + 板块成分 |
| `news` | 新闻 | eastmoney | 个股新闻 + 全球资讯 |
| `em_hot` | 舆情 | eastmoney | 人气榜 + 热门概念 |
| `ths_signal` | 信号 | 10jqka | 同花顺热榜 + 热点原因 + 北向 |
| `sina_option` | 期权 | sina | ETF 期权 codes/tquote/greeks |
| `sina_finance` | 财报 | sina | 新浪财报三表 |
| `cninfo` | 公告 | cninfo | 巨潮公告 + 互动易（orgId 两步 + 两步 POST） |
| `cls_telegraph` | 新闻 | cls | 财联社电报（md5(sha1) 签名） |
| `legulegu` | 估值 | legulegu | 全市场 PE/PB 历史（CSRF 会话） |
| `chip` | 筹码 | eastmoney | 筹码分布（cyq.js + 百度降级） |
| `docs` | 文档 | cninfo/eastmoney | 公告/研报 PDF 原文下载（bytes + LRU） |

## 安装与启动

```bash
cd packages/asgk-server
uv sync
```

需要 Python ≥3.11，[uv](https://docs.astral.sh/uv/) 包管理器。

### 手动启动

```bash
# 默认（端口 7701，运行时文件写入包内 cache/state/logs）
uv run asgk-server

# 生产推荐：固化状态目录到 XDG 数据目录
uv run asgk-server \
  --host 127.0.0.1 --port 7701 \
  --cache-dir ~/.local/share/asgk-server/cache \
  --state-dir ~/.local/share/asgk-server/state \
  --fp-dir ~/.local/share/asgk-server/fingerprints
```

### systemd 用户服务（生产部署）

```bash
# 安装（uv tool install + 写 unit + enable）
./scripts/asgk-server-service.sh install

# 启动 / 停止 / 状态
./scripts/asgk-server-service.sh run
./scripts/asgk-server-service.sh status
systemctl --user journalctl -u asgk-server.service -f
```

服务目录默认 `~/.local/share/asgk-server/`（可通过 `SERVER_WORK_DIR` 覆盖）。

## 客户端配置

asgk 客户端（`skills/a-stock-data/scripts/asgk/`）通过环境变量指向本服务端：

```bash
export ASGK_SERVER=http://127.0.0.1:7701
```

未设置时，asgk 客户端回退旧路径（`em_get` 经 sgw 网关，需 `ASGK_GW`）。新部署
应同时设 `ASGK_SERVER` 并停止 sgw。

## 接口

- `POST /v1/<capability>`：语义请求，body 是参数 JSON。返回结构化数据或 base64
  编码的文档 bytes（`{"data": ..., "cache": "MISS|HIT-MEM|HIT-DOC", "source": ...}`）。
- `GET /v1/sources?capability=<name>`：该能力支持的源列表。

```bash
# 示例：查 600519 实时行情
curl -s -X POST http://127.0.0.1:7701/v1/quote \
  -H 'Content-Type: application/json' \
  -d '{"codes": ["600519"]}'

# 示例：列某能力的源
curl -s 'http://127.0.0.1:7701/v1/sources?capability=docs'
```

## 流量内核（从 sgw 原样搬入）

- **TokenBucket**：按域名组令牌桶限流（config.toml 的 `[[group]]` rps）
- **CircuitBreaker + CircuitStateManager**：403/429 立即熔断 + SQLite 状态闩
  （家庭 IP 无法快速更换，熔断跨重启，canary 探针租约 120s）
- **SingleFlight**：同请求并发 miss 合并（只一个 leader 出网）
- **SemanticCache + DocumentCache**：结构化数据缓存（六类数据型 TTL/persist）+
  文档缓存（bytes 文件 + 20MB/2GB 上限 + LRU 淘汰）

详见 `.agents/notes/capability-proxy-design.md`（架构）与
`.agents/notes/capability-proxy-plan.md`（迁移计划）。

## 测试

```bash
uv run --project packages/asgk-server pytest packages/asgk-server/tests -q
```
