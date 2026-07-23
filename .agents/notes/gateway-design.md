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

基于对 ref 全部 43 端点返回数据时效性的实证分析（见 §3.4.1），将数据归为 **P/L/S/R/N 五档**，每档独立缓存策略。核心思路：**命中率优先**（静态/日级数据长缓存，1000 agent 查同一票只打 1 次外网），**实时性保真**（实时/流式数据不缓存或秒级）。

缓存 key = `方法名 + 参数哈希`（如 `eastmoney_concept_blocks|600519`）。命中即零外网请求、零限流等待——这是降流量的主力。

#### 3.4.1 端点时效性分类（43 端点实证）

| 档 | 含义 | 端点（数） | 代表 |
|----|------|-----------|------|
| **P 静态** | 发布即定稿，永不改 | 6 | 个股/行业研报、研报PDF、分红历史、F10公司资料、巨潮公告、互动易 |
| **L 低频** | 季度更新（财报披露季） | 3 | 季报快照(EPS/ROE)、财报三表、股东户数 |
| **S 半静态** | 日级更新（盘后定稿/日内慢变） | 13 | 一致预期EPS、板块归属、龙虎榜、解禁日历、融资融券、大宗交易、资金流120日、个股基本面、昨涨停池、期权合约清单、概念命中 |
| **R 实时** | 盘中秒级，有实时价值 | 13 | 分钟K、五档盘口、逐笔成交、腾讯行情、北向分钟、分钟资金流、行业排名、涨停/炸板/跌停池、打板情绪、期权T型报价、希腊字母、热榜、人气榜 |
| **N 流式** | 持续推送，每条新内容 | 3 | 个股新闻、财联社电报、全球资讯 |

完整端点→档位映射见 §3.4.4。

#### 3.4.2 五档缓存策略

```toml
[cache]
# P 静态: 事件定稿型,永不改 → 长缓存(30天)
P_ttl = 2592000           # 30天。研报/公告/分红/F10/互动易
# 注: 日K线/百度K线不入此档——取最近N根必含今日盘中实时根,且除权日历史根会变(见 R→S 双态)

# L 低频: 季度更新 → 非财报季长缓存,披露季(1/4/8/10月)前后缩短
L_ttl = 86400             # 1天(默认);财报披露月可配 3600(1h)

# S 半静态: 日级定稿 → 按交易日盘后(18:00)刷新,缓存到次日盘前
S_ttl_session = 0         # 盘中(09:00-18:00): 不缓存(怕拿到昨日残值)
S_ttl_afterclose = 43200  # 盘后(18:00-次日09:00): 12h(次日盘前失效)

# R 实时: 秒级 → 不缓存(默认)
R_ttl = 0                 # no-cache

# N 流式: 每条新内容 → 不缓存
N_ttl = 0                 # no-cache
```

各档设计理由：

- **P（静态，30天）**：研报/公告/分红/互动易这类「事件追加型」——单条一经发布永久不变，但列表会增长。缓存 key 区分「取列表」（TTL 内可能漏新条目，可接受；30天后重拉）vs「取单条」（key 含记录id，命中即永久正确）。**缓存价值最高**，TTL 给到 30 天。
  > ⚠️ **日K线/百度K线不属此档**：虽然历史K线定稿不变，但 agent 调 `bars(offset=N)` 取的是「最近 N 根」，结果集**永远含今日那根未定稿的盘中实时 K 线**（close/vol 秒级变）；且 mootdx 返回不复权原始价，**除权除息日历史根也会跳变**。故日K归 R→S 双态（盘中实时/盘后定稿），见 §3.4.3。
- **L（季度，1天）**：财报三表/季报快照随季报披露更新。非披露季缓存 1 天足够（甚至可更长）；披露月（1/4/8/10月）前后缩短到 1h，避免拿到旧季报。
- **S（日级，分时段）**：这是设计的**关键难点**。龙虎榜/融资融券/大宗/资金流120日等是**盘后定稿**——盘中查会返回昨日残值或空。故：**盘中不缓存**（`S_ttl_session=0`，每次实时查，虽慢但避免脏数据），**盘后缓存 12h**（定稿后到次日盘前，1000 agent 共享一份）。靠交易时段感知（§3.4.3）。
- **R（实时，no-cache）**：五档/逐笔/分钟K/盘中资金流等秒级数据，缓存即过时，不缓存。
- **N（流式，no-cache）**：新闻电报每条新内容，不缓存；如需去重可在上层做「已读集合」。

#### 3.4.3 交易时段感知（S 档与 R↔S 切换的关键）

三类端点存在**盘中/盘后双态**，TTL 需随时段切换：

| 端点 | 盘中(09:00-15:00) | 盘后(15:00后) |
|------|------------------|--------------|
| 日K线/百度K线（取最近N根） | R（含今日实时根，no-cache） | S（今日根定稿，缓存12h） |
| 涨停/炸板/跌停池、打板情绪 | R（动态进出场，no-cache） | S（定稿，缓存12h） |
| 行业排名 | R（实时刷新） | S（定稿） |
| 北向分钟流向 | R（分钟延伸） | S（收盘快照） |

网关内置交易日历判断（区分交易/非交易日、盘中/盘后），动态选 TTL。非交易日（周末/节假日）整体按「盘后」处理（数据定稿，长缓存）。

> 交易日历可用 mootdx 取或硬编码年度日历；MVP 阶段先用「工作日 09:00-15:00 视为盘中」的简化判断，P4 实测时校准。

#### 3.4.4 混合档端点的特殊处理

两个端点返回**多时效字段混合**，需特殊处理：

- **`eastmoney_stock_info`（6.3 个股基本面）**：含静态字段（总股本/流通股/上市日期）+ 日级字段（市值/现价）。方案：整体按 S 处理（盘后缓存12h/盘中不缓存），简化实现；若 P1 移植时发现股本查询频繁，可拆成两个函数分别缓存。
- **`iwencai_query`（2.3b 语义查询）**：查财务→L，查行情→S，取决于查询语义。方案：默认按 S（1天），上层按查询类型显式指定 TTL。

#### 3.4.5 端点→档位完整映射（P1 移植时落地为代码标注）

```
P(30d): 个股研报(2.1)·研报PDF(2.1)·行业研报(2.1)·iwencai搜研报(2.3)
        ·分红历史(4.4)·F10公司资料(6.2)·巨潮公告(7.1)·互动易(10.1)
L(1d):  季报快照(6.1)·财报三表(6.4)·股东户数(4.3)
S(分时段): 一致预期EPS(2.2)·板块归属(3.3)·龙虎榜个股(3.5)·全市场龙虎榜(3.8)
          ·解禁日历(3.6)·融资融券(4.1)·大宗交易(4.2)·资金流120日(4.5)
          ·个股基本面(6.3)·最新提示(7.2)·昨涨停池(8.1d)·期权合约清单(9.1a)
          ·概念命中(10.2c)·[盘后]涨停/炸板/跌停池·行业排名·北向收盘快照
R(no-cache): 日K线/百度K线[盘中含今日实时根](1.1/1.3)·分钟K(1.1)·五档盘口(1.1)·逐笔(1.1)
             ·腾讯行情(1.2)·北向分钟(3.2)·分钟资金流(3.4)·行业排名盘中(3.7)
             ·[盘中]涨停/炸板/跌停池(8.1)·涨停揭秘(8.2)·打板情绪(8.3)
             ·期权T型报价(9.1b)·希腊字母(9.1c)·热榜(10.2a)·人气榜(10.2b)
N(no-cache): 个股新闻(5.1)·财联社电报(5.2)·全球资讯(5.3)
```

P1 移植每个 `asgk` 函数时，在函数上标注 `@cache(tier="S")` 之类装饰器，网关侧无需识别 URL 语义——**档位由调用方（asgk 库）声明**，网关只按声明的 TTL 执行。这比网关猜 URL 归类更可靠。这套标注是分档机制的**先验方案**（§3.4.6），其准确性由 §3.4.7 的离线修正持续提升。具体分流机制见 §3.4.6。

#### 3.4.6 分档机制（先验方案 + 离线修正）

分档机制分两段运行，构成闭环：
1. **先验方案**（本节）：预设一套 P/L/S/R/N 规则，冷启动即用、默认运行——不依赖观测数据，保证 Day 1 就有合理的缓存行为。
2. **离线修正**（§3.4.7）：同时持续保存请求/响应指纹，日后离线分析实测变更频率，修正这套先验规则。

先验方案解决「网关怎么知道一个请求属于哪档」。它可能不准（日K线曾误判为静态），但作为初始策略足够可用；准确性靠 §3.4.7 的离线修正持续提升。

**问题**：网关收到的只是 `GET /?u=https://push2.eastmoney.com/api/qt/stock/get&secid=1.600519`——无语义。而 ref 实测发现**同域名甚至同 path 跨档位**：

```
push2.eastmoney.com/api/qt/stock/get           → 五档盘口 (R)
push2.eastmoney.com/api/qt/stock/fflow/kline/get → 分钟资金流 (R)
push2.eastmoney.com/api/qt/clist/get           → 行业排名 (R→S 双态)
push2.eastmoney.com/api/qt/slist/get           → 板块归属 (S)
```

同一个 `push2.eastmoney.com` 域名混了 R 和 S；`clist/get` 自身还是 R→S 双态（参数不同语义不同）。故**靠域名或 URL path 分流档位不可行**。

**先验方案的实现：调用方显式声明（请求头）+ 网关兜底规则**。

前提已验证：ref 中**所有东财请求都走 `em_get()`，零绕过**（19 处调用，0 处直连）。故只需让 `em_get` 携带档位，所有风控源流量都能被正确分流。

**主路径——请求头声明**：给 `em_get` 增加可选 `tier` 参数，转发到网关时带 HTTP 头：

```python
# asgk/em_proxy.py
_TIER_HEADER = "X-Cache-Tier"
def em_get(url, params=None, headers=None, timeout=15, tier=None, **kw):
    gw = os.environ.get("ASGK_GW")
    h = dict(headers or {})
    if tier:                                   # 调用方声明档位
        h[_TIER_HEADER] = tier
    if gw:
        return requests.get(gw, params={"u": url, **(params or {})}, headers=h, timeout=timeout)
    return requests.get(url, params=params, headers=h, timeout=timeout, **kw)

# asgk 库各函数在调用时声明(由 @cache 装饰器注入):
def eastmoney_concept_blocks(code):
    return em_get(SLIST_URL, params=..., tier="S")     # 板块归属→S档
def eastmoney_fund_flow_minute(code):
    return em_get(FFLOW_URL, params=..., tier="R")     # 分钟资金流→R档
```

网关收到请求：读 `X-Cache-Tier` 头 → 查该档 TTL → 执行缓存/限流。头不存在或非法时走兜底。

**兜底——网关默认规则**（给未声明 tier 的裸请求，如调试用的 curl、第三方直连）：

按域名组 + path 模糊匹配给默认档位，配在 `sgw_config.toml`：

```toml
[fallback]
# 默认安全档:未声明的请求一律按 R(no-cache)处理——宁可低命中,不可返回脏数据
default_tier = "R"

# 可显式覆盖的 path 规则(精确档位已由 em_get 声明,这里只兜底裸请求)
[[fallback.rules]]
path_contains = "/report/list"        # 研报
tier = "P"
[[fallback.rules]]
path_contains = "/api/qt/stock/fflow/daykline"  # 日级资金流
tier = "S"
```

兜底规则保守：未识别的默认 `R`（no-cache）——缓存宁缺毋滥，避免把实时数据当静态缓存导致脏读。

**两层为何这样分工**：
- 主路径（请求头）：精确，随 asgk 函数语义走，东财改 path 不影响（档位跟函数绑定不跟 URL 绑定）。
- 兜底（path 规则）：只为非 asgk 入口（curl/第三方）兜底，覆盖不到也没大碍（默认 R 安全）。

**分流与限流的关系**：限流（§3.3）按**域名组**（东财组/同花顺组），与档位正交——无论 R 还是 S，只要是东财域名就进东财组令牌桶。分流（档位）只决定**缓存策略**，不影响**限流分组**。

#### 3.4.7 离线修正：用实测数据更新先验规则

先验方案（§3.4.6）可能不准——人定的 P/L/S/R/N 规则可能判错（日K线曾误判为静态）。本节是分档闭环的第二段：**持续保存请求/响应指纹，日后离线分析实测变更频率，修正先验规则**。

定位：离线修正（事后、人工触发），**非在线自适应**（运行时不自动调 TTL）。在线自适应有冷启动无数据、抖动、动态字段误判等问题，故分档准确性靠"先验 + 定期离线修正"渐进提升，不靠实时自调整。

**采集（网关侧记录）**：每个请求记录一条结构化日志：

```jsonl
{"ts":"2026-07-22T10:05:12","key":"eastmoney_concept_blocks|600519","tier":"S",
 "req_hash":"a3f..","resp_hash":"b81..","session":"intraday","changed":true}
```

字段说明：
- `key`：缓存 key（方法名+参数哈希），同一 key 的多次记录构成一条变更序列。
- `req_hash` / `resp_hash`：请求参数与响应体的哈希。**响应哈希必须剔除动态字段**（见陷阱）。
- `tier`：本次请求声明的档位（来自 §3.4.6 的头）。
- `session`：交易时段（intraday 盘中 / afterclose 盘后 / holiday 非交易日）——S/R 双态端点的变更频率随时段不同，必须带此维度分析。
- `changed`：与该 key 上一次记录的 resp_hash 是否不同。

**响应哈希的陷阱（必须处理）**：部分端点响应体含每次都变的字段，会污染变更检测：
- 东财全球资讯（5.3）请求带 `req_trace=uuid4()`（SKILL 第1765行）→ 响应必含不同 trace → 哈希每次不同 → 误判为高频。
- 五档盘口（1.1c）含 `servertime` → 但本就是 R 档，不影响判断。
- 处理：哈希前剔除已知动态字段（`req_trace`/`servertime`/`request_id` 等），或按 JSON 结构取业务字段子集再哈希。P1 移植时为每个端点标注「需剔除的字段」。

**离线分析（事后跑）**：聚合同 key 的变更序列，算「变更间隔分布」，对照预设 tier：

| 观测到的变更间隔 | 预设 tier | 判定 |
|----------------|----------|------|
| 数天不变 | P | ✅ 一致 |
| 数天不变 | R | ⚠️ 可疑：是否过度实时?可上调为S |
| 分钟级变 | P | ❌ 矛盾：误判静态,必须下调(日K线案例) |
| 盘后不变+盘中变 | S | ✅ 一致(双态正确) |
| 盘后不变+盘中变 | P | ❌ 矛盾：实为R→S双态,非纯静态 |

分析产出一份「分档修正建议表」，回填到 asgk 的 `@cache(tier=)` 标注和 `sgw_config.toml` 兜底规则——这就是先验方案被离线数据持续修正的闭环。

**与 P4 的衔接**：离线修正作为 `skill-integration-test.md`（P4）的一部分——压测期间积累日志，事后分析产出修正表。MVP（P0）阶段先记录日志但不做分析（分析依赖足够样本，需 P4 的并发压测才积累得到）。

### 3.4.8 磁盘持久化（P/L 档）

五档缓存（§3.4.2）默认纯内存，网关重启即全丢。P 档（30 天 TTL，研报/分红/F10）与 L 档（1 天，财报/股东户数）是**发布即定稿/季度更新**的长效数据，重启后冷启动重打外网既浪费限流配额（≤1 req/s 下回填慢）也增加封 IP 风险。本节为 P/L 增加磁盘持久层。

**定位**：仅持久化 P/L。S 档盘中 0、盘后 12h，跨重启无意义且易脏，不落盘；R/N 本就 no-cache。

**选型**：SQLite（stdlib `sqlite3`，零新依赖，符合 §6 零重型依赖）单文件 `sgw_cache.db`，WAL 模式（读不阻塞写）。schema：`cache(key PK, body BLOB, headers TEXT, expire REAL, tier TEXT, created REAL)`。key 复用内存格式 `f"{tier}|{url}"`；headers 用 `json.dumps` 序列化（值是 `{"Content-Type":...}` 简单 dict）；body 用 BLOB 存原始字节，无 base64 开销。

**写策略**：write-through--`cache.set` 时同步写内存和磁盘。P/L 写入受 ≤1 req/s 限流，频率低，同步落盘开销可接受，且数据不丢。崩溃（`kill -9`）至多丢最后一条在途写入。

**读路径**：内存优先（`HIT-MEM`）；内存未命中回查磁盘，命中则回填内存并返回 `HIT-DISK`，后续命中走内存。响应头 `X-Cache` 由原 `HIT`/`MISS` 细化为 `HIT-MEM`/`HIT-DISK`/`MISS`。

**删除机制**（无后台线程，对齐项目「无后台清理」风格）：
- **启动 `load_all`**：`SELECT *` 回填内存，同时 `DELETE WHERE expire<=now` 清掉历史过期项。
- **`get` 惰性删除**：命中过期项时 `DELETE` 并返回 miss。
- 永不再访问的 P 档 key 最长留存到下次重启才被 `load_all` 清掉。30 天 TTL 内单 IP 场景 P 档总量有限（研报/分红/财报，数千条量级），泄漏可接受。

**配置**（`config.toml` `[cache.persist]`，仿 `[cache.session]`/`[fingerprint]`）：
```toml
[cache.persist]
enabled = true
dir = "cache"          # 相对包目录；生产用 --cache-dir /var/lib/sgw
tiers = ["P", "L"]
```
CLI `--cache-dir` 覆盖 `dir`（仿 `--fp-dir`）。db 默认 `packages/sgw/sgw/cache/sgw_cache.db`。

**观测**：`/__stats` 增加 `disk_cache`（size/hits/misses）、`disk_load_count`/`disk_load_ms`（启动回填条目数与耗时）。详见 §3.6。

**关停**：`main()` 在 `server.shutdown()` 前关闭 db 连接；新增 `SIGTERM` handler 走同一关停路径（原仅 `KeyboardInterrupt`，`kill` 会丢连接）。

**tier bug 关联**：实施时发现 `capital.dividend_history`（`@source` 标 P）与 `holder_num_change`（标 L）经 `_datacenter` 调用时未传 tier，被默认值 `S` 覆盖，运行时实际走 S 档--既与声明不符，也使二者无法落 P/L 磁盘档。已一并修复（显式传 `tier='P'/'L'`）。

### 3.5 retry / 降级
- 429/5xx：指数退避重试（对齐上游 Retry 配置）。
- 403：**不重试**（东财风控信号），返回错误让上层切备用源（ref 的 failover）。

### 3.6 观测
两类观测：
- **计数器**（实时）：每组请求数/缓存命中数/限流等待数/错误数/磁盘缓存。`GET /__stats` 暴露 JSON，供 `test-method.md` 的 L2 压测采集。字段：`cache`（内存 size/hits/misses）、`disk_cache`（磁盘 size/hits/misses，未启用为 null）、`disk_load_count`/`disk_load_ms`（启动回填条目数与耗时，§3.4.8）。响应头 `X-Cache` 区分 `HIT-MEM`/`HIT-DISK`/`MISS`。
- **响应指纹日志**（积累用）：每个请求记一条 §3.4.7 的结构化日志（key/tier/resp_hash/session/changed），落盘为 jsonl。P0 阶段开启记录、不做分析；P4 压测后离线分析产出分档修正表。日志需定期轮转避免膨胀。

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
两个独立包各自的 pyproject 注册 entry point：`sgw-proxy = "sgw.proxy:main"`（网关，sgw 包）、`asgk = "asgk.cli:main"`（CLI，asgk 包，P1 填充）。项目用 uv workspace 管理，`uv run sgw-proxy` / `uv run asgk ...` 即可调用，无需手动 `pip install`。两个包可分别部署（装网关的机器不必装 asgk）。

## 五、产物文件结构

网关与业务库是两个独立包（uv workspace），部署单元分离：

```
skills/a-stock-data/scripts/
├── pyproject.toml              workspace 根（协调两子包，不含代码）
├── sgw/                        网关包（独立部署的基础设施）
│   ├── pyproject.toml          name="sgw", entry: sgw-proxy
│   └── sgw/
│       ├── proxy.py            网关进程（原 sgw_proxy.py）
│       └── config.toml         限流组/缓存TTL配置（原 sgw_config.toml）
└── asgk/                       业务库包（每 agent 用）
    ├── pyproject.toml          name="asgk", entry: asgk(P1后期)
    └── asgk/
        ├── __init__.py         暴露 em_get, eastmoney_reports, ...
        ├── em_proxy.py         em_get（网关/直连自适应）
        ├── _contract.py        @source 装饰器
        ├── reports.py / ...    各层取数（P1 移植）
        └── cli.py              asgk CLI entry point（P1 后期）
```

## 六、技术选型理由

- **同步 Python 而非 async**：单机 localhost 代理，瓶颈是外网东财（本身就要限到 1 req/s），不是网关并发；同步实现最简单、最易调试。1000 agent 的连接由 OS 队列处理，同步串行处理不影响限流目标。
- **标准库 http.server 而非 Flask**：零依赖，部署只需 python3。
- **透明代理而非自定义协议**：agent 代码改动最小（只换 url 前缀），且天然支持 ref 现有的所有东财 URL。

## 七、落地阶段

- **P0（`gateway-mvp.md`）**：实现 `sgw_proxy.py` + `asgk/em_proxy.py`（网关本体 + em_get 兼容），验证"1000 并发不封 IP"。
- **P1（`scripts-library-port.md`）**：移植各层函数时，按本方案的 CLI 命令规划组织模块，并加 `cli.py`。

## 八、验收标准（承接 gateway-mvp.md）

- 网关启动：在 `skills/a-stock-data/scripts/` 下 `uv run sgw-proxy` 监听 7700（等价 `uv run python sgw_proxy.py`）。
- 单进程基准：`curl 'localhost:7700/?u=<东财url>&secid=1.600519'` 正确代理。
- **并发验证**：多进程并发打网关，外网出口被压到东财组 ≤1 req/s（用 `/__stats` 验证）。
- 缓存命中时零外网请求。
- `asgk quote 600519`（直连）和 `asgk report 600519`（经网关）都能返回真实数据。
- `em_get` 接口与上游签名一致；未设 `ASGK_GW` 时行为向后兼容。
