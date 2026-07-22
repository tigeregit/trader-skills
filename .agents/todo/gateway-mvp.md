# TODO: P0 流量网关 MVP

来源：notes/design.md 轴 2 / 落地路线 P0

> **详细设计见 `notes/gateway-design.md`**（含网关架构、按域名组限流、缓存分档、skill CLI/asgk 接入契约、验收标准）。本文件是实现待办清单。

## 背景

上游 `ref/a-stock-data` 的 `em_get()` 用模块级变量 `_em_last_call` 做限流——这是**进程内**限流。本项目单 IP 下 100～1000 个 agent 各自独立进程，每个进程有自己的 `_em_last_call`，互不可见 → 全局并发无上限 → 东财/同花顺必然封 IP（社区实测阈值：>5 req/s 或并发 ≥10 或 1 分钟 ≥200 次）。

必须引入**跨进程的共享限流点**：本地代理网关。

## 待办

- [ ] 实现 `skills/a-stock-data/scripts/sgw_proxy.py`：本地 HTTP 代理，监听 `localhost:PORT`。
- [ ] **按域名组限流先跑通**：东财组（`*.eastmoney.com`，9 子域）全局令牌桶 ≤1 req/s + 随机抖动；同花顺组（`*.10jqka.com.cn`，4 子域）独立桶。对齐上游 `EM_MIN_INTERVAL=1.0`，但从进程级提升到全局级（见 `notes/gateway-design.md` §3.3）。
- [ ] 五档缓存框架（见 `notes/gateway-design.md` §3.4）：按 `方法名+参数哈希` 做 key，TTL 由调用方（asgk 库）用装饰器声明档位（P/L/S/R/N），网关按声明执行。MVP 先落地 P(30d)/L(1d) 长缓存 + R/N no-cache；S 档的分时段（盘中不缓存/盘后12h）与交易时段感知留 P1/P4 校准。
- [ ] **分流机制**（§3.4.6）：`em_get` 带 `X-Cache-Tier` 头声明档位；网关读头执行，无头时按 path 兜底规则（默认 R 安全档）。
- [ ] **响应指纹日志**（§3.4.7）：每个请求记一条 jsonl（key/tier/resp_hash/session/changed），哈希剔除动态字段（req_trace/servertime）。P0 只记录不分析，为 P4 离线校准积累数据。
- [ ] 429/5xx 指数退避；403 不重试（风控信号，降频应对）。
- [ ] 配置文件 `sgw_config.toml`：各组阈值、TTL 表、兜底 path 规则。

## 验收标准

- 网关启动后，单进程基准：`curl localhost:PORT/em?u=<东财url>` 能正确代理并限流。
- **并发验证**：多进程/多请求并发打网关，外网实际出口速率被压到 ≤1 req/s（用网关计数器验证，而非各自计时）。
- 缓存命中时零外网请求。

## 依赖

- 无前置；这是后续所有改造的基础设施。
- 完成后用 `notes/test-method.md` 的流程做并发压测校准阈值。
