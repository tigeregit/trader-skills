# Draft Plan: 合成 akshare 能力到 a-stock-data

> **状态**：Draft，待评审
> **分支**：`feat/akshare-merge`
> **日期**：2026-07-26
> **来源**：基于 temp/akshare 源码核对 + asgk 契约核对起草

---

## 0. TL;DR

**决策**：把 akshare 的 A 股独有能力**合成进现有 `skills/a-stock-data`**，不新增独立 skill。
akshare 仅作 ref 蓝本，移植接口用 asgk 现有架构（`em_get`/`_datacenter`/`@source`/sgw 网关）重写，**零基础设施重构**。

**规模**：分 3 阶段，P0（9 接口）→ P1（11 接口）→ P2（按需）。每阶段独立可交付、可回滚。

**前置约束（不可协商）**：
- AGENTS.md §2 流量管控：东财/同花顺源**必须**经 sgw 网关，禁止直连
- AGENTS.md §6：基于 ref 改造，不 `pip install akshare`
- 所有移植函数必须挂 `@source` 声明 tier/via，纳入缓存分档

---

## 1. 背景与决策依据

### 1.1 调研结论（双向差异）

**akshare 相对 a-stock-data 独有**（广度优势，200+ A 股接口）：
1. 股东维度：十大股东/十大流通股东明细、股东协同、一致行动人
2. 估值历史数据源：个股 PE/PB/股息率分位、全市场 PE/PB、股债利差、巴菲特指标
3. 业绩三件套：业绩预告/业绩快报/预约披露（独立全市场扫描）
4. 筹码分布 + 主力成本（`stock_cyq_em`，独有）
5. 概念板块 → 成份股反向查询 + 板块历史K线
6. 四个事件类：股权质押、商誉、高管增减持、股票回购
7. 股票池构建：A+H 溢价、次新股池、ST/退市名单、IPO 全流程
8. 同花顺技术选股 11 类（突破/放量/洗盘等现成清单）

**a-stock-data 相对 akshare 独有**（深度+架构优势）：
1. 通达信 mootdx TCP 层（五档盘口 46 字段、逐笔、季报快照、F10）— akshare 完全不用
2. 腾讯实时报价完整字段（PE/PB/市值/换手/涨跌停一把抓）— akshare 走东财快照
3. 百度K线带均线 — akshare 无百度K线
4. 打板情绪温度计（炸板率/连板梯队聚合）— akshare 只给原始四池
5. PEG / 前向PE / PE消化年数（本地估值计算）— akshare 只给原始 EPS
6. **架构级**：sgw 共享网关 + `@source` 缓存分档 + failover 矩阵 — akshare 作为单 agent 库完全没有

### 1.2 为什么"合成"而非"拆 2 skill"

曾考虑拆 2 个 skill（实时 vs 研究），推翻理由：

| 维度 | 拆 2 skill | 合成 1 skill |
|------|-----------|-------------|
| 基础设施重构 | **必须**（`em_get`/`@source`/failover 下沉到 `packages/asgk-core/`） | **不用**（akshare 接口 9/10 走东财，复用现成） |
| 交叉接口归属 | 每次判断（龙虎榜/融资融券/三表归哪边） | 不存在（同 skill 内主源+备源） |
| agent 路由层数 | 2 层（先选 skill 再选 reference） | 1 层（直接选 reference） |
| 代码重复 | 4 项完全等价接口需协调 | 0 |
| progressive disclosure | 一般 | **更好**（references 分层是原设计） |

**关键证据**：核对 akshare 10 个 P0 接口的真实数据源，9 个走东财域名（`datacenter-web.eastmoney.com` / `push2his.eastmoney.com` / `push2.eastmoney.com`），**全部命中 sgw 现有限流组**（`PROXIED_DOMAIN_SUFFIXES = (.eastmoney.com, .10jqka.com.cn)`），无需新增网关分组。合成方案的基础设施成本确实为零。

### 1.3 边界声明（合成后的 skill 自我描述）

`a-stock-data` 涵盖「实时交易时序数据 + 研究型结构性数据」两类，经同一 sgw 网关。
不再额外拆分；akshare 上游仅作 ref，按需移植，不追新。

---

## 2. 合成后的目标结构

```
skills/a-stock-data/
├── SKILL.md                  # 路由表扩为「实时数据 / 研究数据」二级分组（< 300 行）
├── references/
│   ├── (现有 11 个不动)
│   │   quote / signal / capital / base / report / news / announce
│   │   limitup / option / sentiment / valuation / failover
│   └── (新增 8 个，按 P0/P1 分批落)
│       ├── holders.md        # P0: 十大股东/流通股东/股东协同
│       ├── valuation_hist.md # P0: 个股 PE/PB 分位（乐咕）
│       ├── earning.md        # P0: 业绩预告/快报/预约披露
│       ├── chip.md           # P0: 筹码分布/主力成本
│       ├── board.md          # P0: 板块成份股反向 + 板块K线
│       ├── risk_event.md     # P0: 高管增减持/回购/机构调研/行业PE
│       ├── macro_value.md    # P1: 全市场择时（巴菲特/股债利差/拥挤度/破净）
│       └── pool_filter.md    # P1: ST/次新/A+H/IPO + 质押/商誉/限售明细
└── scripts/asgk/asgk/
    ├── (现有 12 个模块不动)
    └── (新增 8 个，与 references 一一对应)
        ├── holders.py
        ├── valuation_hist.py
        ├── earning.py
        ├── chip.py
        ├── board.py
        ├── risk_event.py
        ├── macro_value.py
        └── pool_filter.py
```

**SKILL.md 路由表写法**（二级分组，暗示交叉但不重复）：

```markdown
## 实时数据（盘中/日级，R-S 档）
| 需求 | 函数 | 参考 |
| 实时价/PE/PB | tencent_quote | quote |
| ...（现有内容保持不变） |

## 研究数据（季度/事件定稿，L-P 档）
| 需求 | 函数 | 参考 |
| 十大股东明细 | top10_holders | holders |
| 历史PE/PB分位 | pe_pb_percentile | valuation_hist |
| 业绩预告 | earning_forecast | earning |
| 筹码分布/主力成本 | chip_distribution | chip |
| 板块成份股 | board_constituents | board |
| 高管增减持/回购 | mgmt_trade / repurchase | risk_event |
| ST/次新/A+H名单 | pool_filters | pool_filter |
```

**description 字段**（互指消失，改为内部分组提示）：

```
A股全量取数——实时行情(K线/五档/PE/PB/市值)、盘口逐笔、研报与一致预期EPS、
信号(热点/北向/龙虎榜/解禁/行业)、资金面(融资融券/大宗/股东户数与十大股东/
分红/资金流)、财务三表与F10、公告、新闻流、打板(涨停池/情绪温度计)、
ETF期权、舆情、估值(PEG/PE分位)、股东明细、业绩预告/快报、筹码分布、
板块成份股、质押/商誉/回购/高管增减持、ST/次新/A+H/IPO、技术选股。
覆盖实时交易时序与研究型结构性两类数据，经共享流量网关支持单 IP 多 agent 并发。
仅在需要取数时使用。
```

---

## 3. 阶段拆解

每阶段遵循 AGENTS.md §5 的 commit 规则：**一 commit = 一逻辑变更，代码库始终可工作**。

### 阶段 0：基础设施核查（不重构，仅确认）

> **修订提示（2026-07-27）**：后续可行性分析（[akshare-port-feasibility.md §6.4](akshare-port-feasibility.md)）建议在阶段 0 与阶段 1 之间补**阶段 0.5（实现 `_signing.py`/`_htmltable.py`/`_dataframe.py` + vendor JS）**和**阶段 0.6（清理 pyproject.toml 依赖声明）**。阶段 1（P0 纯 JSON）不依赖它们，阶段 2（P1 难点）依赖。本节阶段编号暂未调整，审核时请结合该修订建议。

- [ ] 确认 sgw 网关 `PROXIED_DOMAIN_SUFFIXES` 已覆盖东财/同花顺 → ✅ 已核对（`packages/sgw/sgw/proxy.py:37`）
- [ ] 确认 `em_get` / `_datacenter` / `@source` 契约稳定 → ✅ 已核对
- [ ] **新增**：乐咕网（`legulegu.com` / `eniu.com`）限流决策
  - 乐咕是非风控源（无 IP 封禁历史），按 AGENTS.md §2 可直连
  - 但需在 `asgk` 侧加进程内自律限流（对齐 `em_proxy._direct_throttle` 模式）
  - **决策点**：是放进 sgw 网关新建 `legu` 组，还是 asgk 内直连+自律限流？
    - 倾向 **asgk 内直连**（乐咕无封 IP 风险，进网关反而增加单点依赖）
- [ ] **不动作**：不动 `packages/sgw/`，不动 `em_proxy.py`，不动 `_contract.py`

**交付物**：一份 `.agents/notes/legu-source-decision.md`（30 行内，记录乐咕源处置决策）
**回滚**：无代码变更，仅文档

---

### 阶段 1：P0 移植（9 接口，最高优先级）

每接口一个 commit，便于二分定位。每个 commit 包含：函数实现 + reference 文件 + SKILL.md 路由表新增一行 + `__init__.py` 导出。

| 序 | 接口 | akshare 蓝本 | asgk 函数 | 源 | via | tier | 新模块 |
|----|------|-------------|----------|----|----|------|--------|
| 1.1 | 十大股东/十大流通股东 | `stock_gdfx_top_10_em` / `_free_top_10_em` | `top10_holders(code)` | 东财 datacenter | gateway | L | holders.py |
| 1.2 | 股东持股变化/协同 | `stock_gdfx_holding_change_em` / `_teamwork_em` | `holder_change(code)` / `holder_teamwork(code)` | 东财 datacenter | gateway | L | holders.py |
| 1.3 | 个股 PE/PB 分位 | `stock_a_indicator_lg` | `pe_pb_percentile(code)` | 乐咕 | direct（自律限流） | L | valuation_hist.py |
| 1.4 | 业绩预告/快报 | `stock_yjyg_em` / `stock_yjkb_em` | `earning_forecast(code)` / `earning_express(code)` | 东财 datacenter | gateway | L | earning.py |
| 1.5 | 筹码分布 | `stock_cyq_em` | `chip_distribution(code)` | 东财 push2his | gateway | S | chip.py |
| 1.6 | 概念/行业板块成份股 | `stock_board_concept_cons_em` / `_industry_cons_em` | `board_constituents(board)` | 东财 push2 | gateway | S | board.py |
| 1.7 | 高管增减持 | `stock_hold_management_detail_em` | `mgmt_trade(code)` | 东财 datacenter | gateway | S | risk_event.py |
| 1.8 | 股票回购 | `stock_repurchase_em` | `repurchase(code)` | 东财 datacenter | gateway | P | risk_event.py |
| 1.9 | 机构调研 | `stock_jgdy_detail_em` | `institute_research(code)` | 东财 datacenter | gateway | S | risk_event.py |

**阶段验收标准**：
- [ ] 9 个函数全部挂 `@source`，tier/via 声明正确
- [ ] 每函数配 reference 文件（< 50 行，含调用示例 + 字段说明）
- [ ] SKILL.md 路由表新增「研究数据」分组
- [ ] `__init__.py` 导出 9 个新函数
- [ ] 真机验证：每个函数至少 1 个 code 实测返回非空（用 600519/000001 验证）
- [ ] 经网关验证：东财源确认走 `em_get`/`_datacenter`，乐咕源确认走直连+限流

**回滚**：9 个独立 commit，可逐个 revert

---

### 阶段 2：P1 移植（11 接口，补强价值）

| 序 | 接口 | asgk 函数 | 源 | via | tier | 新/扩模块 |
|----|------|----------|----|----|------|----------|
| 2.1 | 全市场 PE/PB 历史 | `market_pe_pb()` | 乐咕 | direct | L | macro_value.py |
| 2.2 | 股债利差 / 巴菲特指标 | `equity_bond_spread()` / `buffett_indicator()` | 乐咕 | direct | L | macro_value.py |
| 2.3 | 破净股/创新高统计/拥挤度 | `market_bread()` | 乐咕 | direct | S | macro_value.py |
| 2.4 | 股权质押（市场+个股） | `pledge_ratio(code)` / `pledge_overview()` | 东财 datacenter | gateway | S | pool_filter.py |
| 2.5 | 商誉（市场+个股） | `goodwill(code)` / `goodwill_overview()` | 东财 datacenter | gateway | L | pool_filter.py |
| 2.6 | 限售解禁明细（按股东/队列） | `lockup_detail(code)` / `lockup_queue()` | 东财 datacenter | gateway | S | 扩 signal.py（已有 `lockup_expiry`） |
| 2.7 | 次新股池/打新收益率 | `sub_new_pool(date)` / `ipo_yield()` | 东财 push2ex | gateway | S | pool_filter.py |
| 2.8 | 新股 IPO 全流程 | `ipo_pipeline()` | 东财 datacenter | gateway | P | pool_filter.py |
| 2.9 | 停复牌全市场 | `suspension_pool()` | 东财 datacenter | gateway | S | pool_filter.py |
| 2.10 | A+H 溢价/行情 | `ah_premium(code)` | 东财 datacenter | gateway | R | pool_filter.py |
| 2.11 | ST/退市/次新名单 | `pool_filters(category)` | 东财 push2 / 交易所 | gateway/direct | S | pool_filter.py |

**阶段验收标准**：同阶段 1
**回滚**：11 个独立 commit

---

### 阶段 3：交叉接口容灾化 + P2 按需

#### 3a. 交叉接口容灾化（高价值，提升鲁棒性）

把 akshare 同名接口作为 a-stock-data 主源的**第二源**补进现有模块，写入 `references/failover.md`：

| 现有主源 | 补 akshare 第二源 | asgk 新函数 | 价值 |
|---------|------------------|------------|------|
| `margin_trading`（东财） | 沪深交易所官方 | `margin_trading_sse/szse` | 东财被封时兜底 |
| 财报三表（新浪） | 东财 `stock_three_report_em` | `em_balance_sheet` 等 | 多源冗余 |
| 龙虎榜（东财个股） | 东财营业部/机构统计 | `lhb_yyb_rank` | 维度补强 |
| 涨停四池（东财） | 强势股池/次新股池 | `em_strong_pool` / `em_sub_new_pool` | 维度补强 |
| 一致预期 EPS（同花顺） | 东财 `stock_profit_forecast_em` | `em_eps_forecast` | 源冗余 |

#### 3b. P2 按需移植（不列时间表，按实际需求触发）

- 同花顺技术选股 11 类（`stock_rank_*_ths`）
- 千股千评主力控盘/机构参与度（`stock_comment_detail_*`）
- 雪球/百度舆情多源
- ESG 评级 / 杜邦对比 / 一致行动人
- 股东大会/股市日历

---

## 4. 交叉接口归属表（合成方案的核心优势）

合成后无需决策"归哪个 skill"，只需标注主源/备源。下表写入 `references/failover.md`：

| 能力 | 主源 | 备源（akshare 移植） | 关系 |
|------|------|---------------------|------|
| 龙虎榜 | 东财个股+全市场（现有） | 东财营业部/机构统计 | 维度互补 |
| 融资融券 | 东财明细（现有） | 沪深交易所官方 | 源不同（容灾） |
| 北向资金 | hexin.cn 直连（现有） | 东财持股明细 | 维度互补 |
| 财联社电报 | cls.cn 本地签名（现有） | — | 完全等价，不重复 |
| 财报三表 | 新浪（现有） | 东财三表 | 源不同（容灾） |
| 涨停四池 | 东财四池（现有） | 强势/次新池 | 维度互补 |
| 板块 | 个股→概念命中（现有） | 板块→成份股 | 方向不同 |
| 研报 | 东财个股研报（现有） | — | 完全等价，不重复 |
| 一致预期 EPS | 同花顺（现有） | 东财 | 源不同（容灾） |
| 互动易 | 巨潮 irm（现有） | — | 完全等价，不重复 |
| 股东户数 | 东财变化（现有） | 十大股东明细 | 维度互补 |

---

## 5. akshare 仓库处置

**建议**：移到 `ref/akshare`，与 `ref/a-stock-data` 并列，作为长期移植蓝本。

理由：
- 移植是多阶段过程（P0→P1→P2 跨多次会话），需要稳定的 ref 位置
- `ref/` 的语义就是"只读对照，不直接用于生产"（AGENTS.md §3）
- 与 `ref/a-stock-data` 同等地位，二者都是 asgk 的改造蓝本

**操作**：
- 从 `.agents/temp/akshare` 移到 `ref/akshare`
- 加入 `.gitmodules`（与 `ref/a-stock-data` 同样以 submodule 形式，或直接 vendor）
- **决策点**：submodule vs vendor？
  - 倾向 **vendor（直接 commit 到 ref/akshare）**：akshare 上游更新频繁，但本项目只取特定 snapshot 作蓝本，不追新；submodule 反而引入同步负担
  - 与 `ref/a-stock-data` 的 submodule 形式不一致，需评审是否破坏一致性

**当前 temp/akshare 的处置**：先保留，待 ref/akshare 落定后删除。

---

## 6. 风险与缓解

| 风险 | 严重度 | 缓解 |
|------|-------|------|
| 乐咕源限流策略不当（高频打被封） | 中 | 阶段 0 先出 `.agents/notes/legu-source-decision.md`，确定直连+自律限流的参数 |
| 东财端点字段漂移（akshare 蓝本版本与现网不一致） | 中 | 每接口真机验证（用 600519/000001），字段缺失时记录在 reference 的"已知限制" |
| SKILL.md 路由表膨胀超 300 行 | 低 | 二级分组 + reference 分层吸收，预估扩到 ~28 行路由（远低于上限） |
| commit 粒度过细（P0 9 接口 9 commit） | 低 | 便于二分定位；若评审认为太碎，可按模块合并（holders 2 接口合 1 commit） |
| akshare 上游接口废弃 | 低 | ref 蓝本不追新；移植时记录 akshare 版本号 |
| 移植引入新依赖 | 低 | 实现层用已装依赖（pandas/lxml/mini-racer 已是 mootdx 传递依赖）；契约层返回 `list[dict]` + `to_df()` 包装。详见 [akshare-port-feasibility.md §5](akshare-port-feasibility.md) |

---

## 7. 不在本计划范围

明确排除，避免 scope 蔓延：
- ❌ akshare 港股/美股/期货/期权（非 ETF）/外汇/加密/基金/债券接口
- ❌ akship 蛋卷基金/宏观中国/经济数据等非 A 股方向
- ❌ 基础设施重构（`em_get`/`@source`/sgw 保持现状）
- ❌ 拆分为多个 skill
- ❌ 追 akshare 上游新版本

---

## 8. 待评审决策点（需用户拍板）

1. **akshare 仓库处置**：`ref/akshare` vendor vs submodule vs 留 temp？
2. **乐咕源限流**：进 sgw 网关新建 `legu` 组 vs asgk 内直连+自律限流？（倾向后者）
3. **commit 粒度**：每接口 1 commit vs 每模块 1 commit？（倾向前者，便于二分）
4. **阶段 1 启动条件**：是否等阶段 0 的乐咕源决策文档落定后才开始 P0 移植？
5. **本 draft plan 的归宿**：晋升到 `.agents/notes/skill-merge-design.md`（长期）vs 留 temp（一次性）？

---

## 9. 下一步（等评审通过后）

1. 评审通过 → 本文档晋升到 `.agents/notes/skill-merge-design.md`
2. akshare 仓库按决策点 1 处置
3. 按阶段 0 → 1 → 2 → 3 顺序执行，每阶段交付后回归测试
4. 在 `.agents/todo/` 拆出对应可执行待办（P0 9 项、P1 11 项）
