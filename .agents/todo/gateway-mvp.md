# TODO: P0 流量网关 MVP

来源：docs/design.md 轴 2 / 落地路线 P0

## 背景

上游 `ref/a-stock-data` 的 `em_get()` 用模块级变量 `_em_last_call` 做限流——这是**进程内**限流。本项目单 IP 下 100～1000 个 agent 各自独立进程，每个进程有自己的 `_em_last_call`，互不可见 → 全局并发无上限 → 东财/同花顺必然封 IP（社区实测阈值：>5 req/s 或并发 ≥10 或 1 分钟 ≥200 次）。

必须引入**跨进程的共享限流点**：本地代理网关。

## 待办

- [ ] 实现 `skills/a-stock-data/scripts/sgw_proxy.py`：本地 HTTP 代理，监听 `localhost:PORT`。
- [ ] **单域名组先跑通**：东财组（`*.eastmoney.com`）全局令牌桶，限流 ≤1 req/s + 随机抖动（对齐上游 `EM_MIN_INTERVAL=1.0`，但从进程级提升到全局级）。
- [ ] 最小缓存：先实现按 URL key 的内存缓存 + 可配置 TTL（静态数据长 TTL、实时数据 no-cache）。
- [ ] 429/5xx 指数退避；403 不重试（风控信号，降频应对）。
- [ ] 配置文件 `sgw_config.toml`：各组阈值、TTL 表。

## 验收标准

- 网关启动后，单进程基准：`curl localhost:PORT/em?u=<东财url>` 能正确代理并限流。
- **并发验证**：多进程/多请求并发打网关，外网实际出口速率被压到 ≤1 req/s（用网关计数器验证，而非各自计时）。
- 缓存命中时零外网请求。

## 依赖

- 无前置；这是后续所有改造的基础设施。
- 完成后用 `docs/test-method.md` 的流程做并发压测校准阈值。
