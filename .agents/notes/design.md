# a-stock-data 转化设计

将 `ref/a-stock-data`（上游 A 股数据工具包）转化为符合本项目部署场景的 skill。

- 上游形态：单文件 `SKILL.md`（127KB / 2815 行），10 层数据架构，43 个端点，15 个数据源。
- 上游场景：**单 agent**，整文件入上下文，进程内 `em_get()` 限流。
- 本项目场景：**单 IP 下 100～1000 个 agent 并发**。

两者的核心矛盾集中在三点：token 效率、并发流量管控、代码复用。本设计用「三轴重构」对应解决。

---

## 一、矛盾分析

| 矛盾点 | 上游现状 | 本项目要求 |
|--------|---------|-----------|
| Token 效率 | 127KB 全量进上下文，每次触发都载入 | 按需加载，单次只用相关层 |
| 并发风控 | 进程内 `em_get()`（模块级 `_em_last_call` 计数） | 1000 进程 = 1000 个独立限流器，东财/同花顺必封 IP |
| 代码复用 | 实现内嵌 markdown，agent 每次重新拼装脚本 | 沉淀为共享库，agent 只调用 |

### 上游限流的失效机理（验证）

上游 `em_get()` 用模块级变量 `_em_last_call = [0.0]` 维护「上次请求时间」：

- 单进程内：有效，串行限流 1 req/s。
- 多进程（1000 agent 各自一个进程/解释器）：每个进程有**独立的** `_em_last_call`，互相不可见 → 全局并发无上限 → 触发东财风控（社区实测阈值：>5 req/s 或并发 ≥10 或 1 分钟 ≥200 次 → 临时封 IP）。

结论：必须引入**跨进程的共享限流点**，即网关。

---

## 二、轴 1：Token 效率 —— 拆分 + 按需加载

目标产物结构：

```
skills/a-stock-data/
├── SKILL.md                 # 路由层：10 层速查表 + 触发描述（目标 <300 行）
├── references/
│   ├── layer1-quote.md      # 行情层：mootdx / 腾讯 / 百度
│   ├── layer2-report.md     # 研报层：东财 / 同花顺 / iwencai
│   ├── layer3-signal.md     # 信号层：热点 / 北向 / 龙虎榜 / 解禁 / 行业
│   ├── layer4-capital.md    # 资金面：融资融券 / 大宗 / 股东户数 / 分红 / 资金流
│   ├── layer5-news.md       # 新闻：东财个股 / 财联社 / 全球资讯
│   ├── layer6-base.md       # 基础数据：mootdx财务/F10 / 东财信息 / 新浪三表
│   ├── layer7-announce.md   # 公告：巨潮 / mootdx
│   ├── layer8-limitup.md    # 打板：涨停/炸板/跌停池 + 题材情绪
│   ├── layer9-option.md     # ETF期权：T型报价 / 希腊字母 / IV
│   ├── layer10-sentiment.md # 舆情：互动易 / 热榜 / 人气榜
│   ├── valuation.md         # 估值公式：前向PE / PE消化 / PEG / full_valuation
│   └── failover.md          # 备用源速查 & 降级策略
└── scripts/                 # 见轴 3
```

**SKILL.md 路由层职责**（精简，不含实现）：

1. 触发描述（`description` 字段，主动式）。
2. 「要什么 → 读哪个 reference / 调哪个脚本」速查表（移植上游「端点路由速查」表）。
3. 数据源优先级总则：能用通达信/腾讯就不碰东财。
4. 风控总则：东财/同花顺请求**一律走网关**（见轴 2）。

**效果**：原 2815 行全量载入 → 单次仅载入路由表（约 150 行）+ 命中的 1～2 个 reference（各约 150–250 行）。单次 token 降幅约 80–90%。

---

## 三、轴 2：并发流量管控 —— 共享网关（本项目独有）

### 设计

部署一个**本地流量网关** `scripts/sgw_proxy.py`，所有 agent 的「带风控源」请求走 `http://localhost:PORT`，由网关在**单 IP 全局**层面串行限流 + 缓存。

```
agent (×1000)                        外网
   │  直连: 通达信(TCP 7709)          │
   │  直连: 腾讯/百度/新浪(HTTP)       │
   │  直连: 巨潮(无风控)  ─────────────┼──► push2.eastmoney.com
   │                                  │
   └─► localhost:PORT (sgw_proxy) ────┘   同花顺 / 财联社
            │  全局令牌桶(按域名分组)
            │  共享缓存(TTL by 数据新鲜度)
            ▼
```

### 网关能力清单

1. **按域名分组的全局令牌桶限流**
   - 东财组（`*.eastmoney.com`）：全局 ≤1 req/s + 随机抖动（对齐上游 `EM_MIN_INTERVAL=1.0`，但从进程级提升到全局级）。
   - 同花顺组（`*.10jqka.com.cn` / `basic.10jqka.com.cn`）：独立桶，独立阈值。
   - 财联社、iwencai 等各一组。组间互不影响。
2. **共享缓存**
   - 静态/低频数据（股本、上市日期、分红历史、F10 文本）：长 TTL（如 6–24h）。
   - 半静态（PE/PB/市值、一致预期 EPS）：短 TTL（如 30–60s）。
   - 实时数据（五档盘口、逐笔、分钟资金流）：`no-cache`，不缓存。
   - 1000 agent 查同一只票 → 命中缓存即零外网请求，是降流量的主力。
3. **统一 retry / 降级**：429/5xx 指数退避；403 不重试（风控信号，降频）。主源被封时网关可自动切到备用源（移植上游「备用源速查」）。
4. **观测**：计数器 / 慢请求日志，便于调阈值。

### agent 侧改造

上游代码块里的 `em_get(url, ...)` 替换为走网关：

```python
# 上游（进程内限流，多进程失效）
from xxx import em_get
em_get("https://push2.eastmoney.com/api/...")

# 本项目（走全局网关）
import os
EM_GW = os.environ.get("ASGK_GW", "http://localhost:7700")
requests.get(f"{EM_GW}/em", params={"u": url, **(params or {})})
```

封装在 `scripts/asgk/em_proxy.py`，agent 只 `from asgk import em_get`，接口不变。

### 为什么是代理网关而非 Redis 分布式限流

- 代理是**唯一串行点**：无论多少进程，外网出口收敛到一个，限流语义最简单、最可靠。分布式限流（Redis）逻辑散落在各 agent，仍有竞争窗口，且 agent 侧代码复杂度高。
- 代理天然承载缓存与降级，Redis 只做计数还得另写缓存层。
- 代价：网关是单点。但本项目场景（单 IP 集群）本就是集中部署，加网关进程成本低，且可后续做主备/水平扩展（按域名分片）。

---

## 四、轴 3：代码沉淀 —— scripts/ 共享库

上游把全部实现内嵌 markdown，agent 每次重新组装。转化为：

```
skills/a-stock-data/scripts/
├── asgk/                    # A 股工具包（共享库，agent import 调用）
│   ├── __init__.py
│   ├── client.py            # tdx_client() 封装 + mootdx BESTIP 规避
│   ├── em_proxy.py          # em_get() → 走网关
│   ├── quote.py             # tencent_quote / baidu_kline_with_ma
│   ├── reports.py           # eastmoney_reports / ths_eps_forecast / iwencai
│   ├── signal.py            # 热点 / 北向 / 龙虎榜 / 解禁 / 行业
│   ├── capital.py           # 融资融券 / 大宗 / 股东户数 / 分红 / 资金流
│   ├── news.py              # 东财新闻 / 财联社 / 全球资讯
│   ├── base.py              # mootdx财务/F10 / 东财信息 / 新浪三表
│   ├── announce.py          # 巨潮公告
│   ├── limitup.py           # 涨停/炸板/跌停池 + 情绪
│   ├── option.py            # ETF期权
│   ├── sentiment.py         # 互动易 / 热榜
│   └── valuation.py         # forward_pe / pe_digestion / calc_peg / full_valuation
├── sgw_proxy.py             # 流量网关（部署一次，全员共用）
└── sgw_config.toml          # 网关配置（各组阈值、TTL 表）
```

**reference 文件 = 该层脚本的使用说明 + 调用示例**，不再内嵌长实现：

```python
# references/layer1-quote.md 里的示例（精简）
from asgk.client import tdx_client
from asgk.quote import tencent_quote

client = tdx_client()
bars = client.bars(symbol="600519", frequency=4, count=20)  # 日K
q = tencent_quote(["sh600519"])                              # PE/PB/市值
```

---

## 五、落地路线（分阶段）

本任务仅完成初始化 + 规范 + 本设计文档。后续落地建议分阶段：

1. **P0 网关 MVP**：`sgw_proxy.py` 单域名组（东财）+ 最小缓存。先验证「1000 agent 走网关不封 IP」。
2. **P1 scripts 库**：按层移植上游函数到 `asgk/`，接口对齐，加网关改造。
3. **P2 references 拆分**：把上游 SKILL.md 的 10 层拆成 reference 文件，实现部分替换为 `asgk` 调用。
4. **P3 SKILL.md 路由层**：写路由表 + 触发描述，串联 references 与 scripts。
5. **P4 实测**：用 pi agent（见 AGENTS.md「测试方法」）跑典型流程（单票估值 / 批量检索），校准网关阈值与缓存 TTL。

---

## 六、与上游的关系

- `ref/a-stock-data` 是**只读蓝本**，本项目 skill 是**改造产物**，不向上游回写。
- 上游版本演进时，`git submodule update` 拉取后 diff 对照，将有用的接口修复/失效处理同步进 `asgk/`。
- 本项目保持**中立**：不引入交易策略，只提供取数与（未来的）下单能力，与上游「数据工具包」定位一致，但形态为多 agent 友好。
