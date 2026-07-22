# Gateway 设计方案（含 skill CLI 接入）

本文件是流量网关 + skill 接入的正式设计文档。对应实现待办：`todo/gateway-mvp.md`（P0）、`todo/scripts-library-port.md`（P1）。

## 一、要解决的问题

1. **并发封 IP**：上游 `em_get()` 用模块级变量 `_em_last_call` 做进程内限流；本项目 1000 agent 各自独立进程 → 每个进程独立计数，全局并发无上限 → 东财（9 子域）/同花顺（4 子域）封 IP（社区实测阈值：>5 req/s 或并发 ≥10 或 1 分钟 ≥200 次）。
2. **CLI 接入**：除 Python 库外，还要提供 `asgk` 命令行工具，让 shell/其他语言能直接取数。

## 二、整体架构

```
┌─────────────┐  ┌──────────────────┐
│ asgk CLI    │  │ asgk Python 库    │   (上层，agent/shell 入口)
│ asgk quote  │  │ from asgk import │
│ asgk report │  │   em_get, quote  │
└──────┬──────┘  └────────┬─────────┘
       │  共用 asgk 核心(同一套取数逻辑)  │
       └──────────┬──────┘
                  ▼
            em_get(url, params)          (统一请求入口，签名与上游兼容)
                  │
        ┌─────────┴──────────┐
        │ ASGK_GW 环境变量?   │
        │ 设了 → 走网关       │──► http://localhost:7700  (全局令牌桶+缓存)
        │ 没设 → 直连(向后兼容)│     │
        └────────────────────┘     ▼
                          sgw_proxy.py (单进程，1000 agent 共享)
                          ├─ 按域名组限流(东财组/同花顺组)
                          ├─ 缓存(静态长TTL/实时no-cache)
                          └─ 透明代理到外网东财/同花顺
```

**两条流量路径**（关键）：
- **风控源**（东财/同花顺）：经网关。CLI 和 Python 库都通过 `em_get` → 检查 `ASGK_GW` → 转发到 `localhost:7700`。
- **无风控源**（腾讯/百度/新浪/mootdx-TCP）：直连，不经网关（避免网关成瓶颈）。mootdx 是 TCP，网关只管 HTTP，故直连。

## 三、网关设计（sgw_proxy.py）

### 3.1 形态
同步 Python 进程（标准库 `http.server` + `urllib`，零重型依赖），监听 `localhost:7700`。1000 agent 通过 localhost 共享这一个限流点。

### 3.2 API（透明代理）
单端点 `GET /`，query 带原始 URL：
```
GET http://localhost:7700/?u=https://push2.eastmoney.com/api/qt/stock/get&secid=1.600519
```
网关解析 `u`，按其域名归组限流 → 查缓存 → 透明 GET 外网 → 原样返回 body/headers。

### 3.3 按域名组限流（核心）
配置 `sgw_config.toml`，每组独立令牌桶：
```toml
[group.eastmoney]
domains = ["*.eastmoney.com"]      # 9 个子域归一组
rps = 1.0                            # 全局 ≤1 req/s（对齐上游 EM_MIN_INTERVAL）
jitter = [0.1, 0.5]                  # 随机抖动

[group.10jqka]
domains = ["*.10jqka.com.cn"]
rps = 1.5                            # 同花顺独立组，独立阈值
```
**全局串行**：无论多少 agent 进程并发到达，网关是唯一出口，令牌桶在网关进程内 → 真正跨进程限流（这是相对上游进程内限流的关键改进）。

需纳管的域名（ref 实测）：
- 东财组（9）：`push2` / `push2his` / `push2ex` / `data` / `reportapi` / `emappdata` / `kuaixun` / `quote` / `so` `.eastmoney.com`
- 同花顺组（4）：`basic` / `data` / `zx` / `dq` `.10jqka.com.cn`

### 3.4 缓存
按 URL+params 做 key，TTL 按数据新鲜度分档（配在 toml）：
```toml
[cache]
static_ttl = 21600      # 股本/上市日期/分红历史/F10文本: 6h
semistatic_ttl = 60     # PE/PB/市值/一致预期EPS: 60s
realtime_nocache = true # 五档/逐笔/分钟资金流: no-cache
```
命中率即零外网请求——1000 agent 查同一票 PE，只打 1 次外网。TTL 分档的归类在 P1 移植各端点时按数据性质标注。

### 3.5 retry / 降级
- 429/5xx：指数退避重试（对齐上游 Retry 配置）。
- 403：**不重试**（东财风控信号），返回错误让上层切备用源（ref 的 failover）。

### 3.6 观测
内置计数器：每组请求数/缓存命中数/限流等待数/错误数。`GET /__stats` 暴露 JSON，供 `test-method.md` 的 L2 压测采集。

## 四、Skill CLI 接入设计（asgk）

### 4.1 三层接入（同一套核心，不同入口）

| 层 | 形态 | 用法 | 适用 |
|----|------|------|------|
| L1 Python 库 | `from asgk import em_get, quote` | agent 写 Python | pi agent / 代码内 |
| L2 CLI | `asgk <command> <args>` | shell/管道 | 非 Python 场景、快速查询、脚本 |
| L3 网关代理 | `em_get(url)` 透明转发 | 底层 | 上述两层都经它访问风控源 |

### 4.2 CLI 命令设计（贴合 agent 实际取数场景）
```
asgk quote <code>              # 实时行情 PE/PB/市值（腾讯，直连）
asgk kline <code> [-f 日线] [-n 20]   # K线（mootdx TCP，直连）
asgk report <code>             # 研报（东财，经网关）
asgk fundflow <code>           # 资金流（东财，经网关）
asgk block <code>              # 板块归属（东财，经网关）
asgk valuation <code>          # 完整估值（多源串联）
```
每个命令 = 调对应 asgk 库函数 → 格式化输出（JSON/表格，`--format json` 可管道）。CLI 自动读 `ASGK_GW` 决定风控源是否走网关。

### 4.3 em_get 兼容契约（零改动迁移）
`asgk.em_get(url, params, headers, timeout)` **签名与上游完全一致**。迁移时 agent 代码只改一行 import：
```python
# 上游: em_get 是模块级函数
# 本项目:
from asgk import em_get     # 接口不变，底层自动走网关
```
环境变量切流（关键，渐进迁移）：
```python
# asgk/em_proxy.py
import os, requests
_GW = os.environ.get("ASGK_GW")  # 设了走网关，没设直连（向后兼容）
def em_get(url, params=None, headers=None, timeout=15, **kw):
    if _GW:
        return requests.get(_GW, params={"u": url, **(params or {})}, timeout=timeout)
    return requests.get(url, params=params, headers=headers, timeout=timeout, **kw)
```
未配 `ASGK_GW` 时行为与上游一致（直连；上游已有的进程内限流保留作 fallback）。

### 4.4 CLI 安装
`pyproject.toml` 注册 entry point：`asgk = "asgk.cli:main"`。`pip install -e .` 后 `asgk` 全局可用。

## 五、产物文件结构

```
skills/a-stock-data/scripts/
├── asgk/
│   ├── __init__.py            # 暴露 em_get, quote, ...
│   ├── em_proxy.py            # em_get（网关/直连自适应）
│   ├── client.py              # mootdx TCP（直连，不经网关）
│   ├── quote.py / reports.py / ...   # 各层取数（P1 移植）
│   └── cli.py                 # asgk CLI entry point
├── sgw_proxy.py               # 网关进程
└── sgw_config.toml            # 限流组/缓存TTL配置
```

## 六、技术选型理由

- **同步 Python 而非 async**：单机 localhost 代理，瓶颈是外网东财（本身就要限到 1 req/s），不是网关并发；同步实现最简单、最易调试。1000 agent 的连接由 OS 队列处理，同步串行处理不影响限流目标。
- **标准库 http.server 而非 Flask**：零依赖，部署只需 python3。
- **透明代理而非自定义协议**：agent 代码改动最小（只换 url 前缀），且天然支持 ref 现有的所有东财 URL。

## 七、落地阶段

- **P0（`gateway-mvp.md`）**：实现 `sgw_proxy.py` + `asgk/em_proxy.py`（网关本体 + em_get 兼容），验证"1000 并发不封 IP"。
- **P1（`scripts-library-port.md`）**：移植各层函数时，按本方案的 CLI 命令规划组织模块，并加 `cli.py`。

## 八、验收标准（承接 gateway-mvp.md）

- 网关启动：`python sgw_proxy.py` 监听 7700。
- 单进程基准：`curl 'localhost:7700/?u=<东财url>&secid=1.600519'` 正确代理。
- **并发验证**：多进程并发打网关，外网出口被压到东财组 ≤1 req/s（用 `/__stats` 验证）。
- 缓存命中时零外网请求。
- `asgk quote 600519`（直连）和 `asgk report 600519`（经网关）都能返回真实数据。
- `em_get` 接口与上游签名一致；未设 `ASGK_GW` 时行为向后兼容。
