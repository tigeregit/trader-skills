# 合成 akshare 能力到 a-stock-data（实施记录）

> **状态**：Implemented，17/17 公共函数完成并通过双平台有限验证
> **分支**：`feat/akshare-merge`
> **最后修订**：2026-08-01
> **关联**：
> - [akshare-integration-analysis.md](akshare-integration-analysis.md)：为什么选 ref-port（架构方案比较）
> - [akshare-port-feasibility.md](akshare-port-feasibility.md)：技术模式与可移植性证据

---

## 0. 参考基线与适用范围

**AkShare 参考基线**（所有"akshare 源码事实"仅针对此 snapshot，不外推到上游最新版）：

- 本地路径：`.agents/temp/akshare`
- version：`1.18.64`
- commit：`fcdbf25aa864a218c54864c3f6ab6a2ed19cce28`
- commit date：`2026-05-27`

> 上游接口签名、字段、host 可能漂移；真正实现时若发现 snapshot 与现网不符，以现网为准并回写本文档，但不追 akshare 新版本。

---

## 1. TL;DR

**决策**：把 akshare 的 A 股独有能力**合成进现有 `skills/a-stock-data`**，不新增独立 skill。akshare 仅作 ref 蓝本，按固定 snapshot 逐接口移植到 asgk 现有架构（`em_get`/`_datacenter`/`@source`/sgw 网关）。**不 `pip install akshare`。**

**基础设施前置已完成**：sgw 和 `_datacenter` 已落实 host 精确归组、请求头透传、query-aware 缓存键与 datacenter 全量分页。详见 §3 阶段 2 和 [integration-analysis §1](akshare-integration-analysis.md)。

**规模**：按 [§4 interface inventory](#4-authoritative-interface-inventory) 的稳定 ID 推进，每个公开 asgk 函数一行、独立验收；不再用"P0 9 接口 / P1 11 接口"这种混淆"能力数 / 上游函数数 / 公开函数数"的口径。

**核心约束**（来自 AGENTS.md，不可协商）：
- §2 流量管控：东财/同花顺风控源**必须**经 sgw 网关，禁止直连；无 IP 风控源可直连。
- §6：基于 ref 改造，不 `pip install akshare`。

---

## 2. 背景与决策依据

### 2.1 双向差异（候选能力领域）

**akshare 相对 a-stock-data 独有**（广度优势，候选领域）：
1. 股东维度：十大股东/十大流通股东明细、股东持股变化（全市场）、股东协同（按股东类型）
2. 估值历史数据源：全市场 PE/PB、指数 PE/PB（乐咕）；**注意 snapshot 中无个股 PE/PB 接口**，个股估值已由现有 `valuation` 层本地计算覆盖
3. 业绩三件套：业绩预告/业绩快报（全市场报告期扫描）
4. 筹码分布 + 主力成本（本地 CYQ 算法，依赖 K 线 + mini-racer）
5. 板块 → 成份股反向查询（概念/行业）
6. 四类事件：股权质押（全市场）、商誉（全市场）、高管增减持（全市场明细）、股票回购（全市场）
7. 机构调研（全市场，按开始日期）

**a-stock-data 相对 akshare 独有**（深度+架构优势，不复述细节）：
mootdx TCP 层、腾讯实时报价完整字段、百度K线带均线、打板情绪温度计、PEG/前向PE/PE消化（本地估值计算）、sgw 共享网关 + `@source` 缓存分档 + failover 矩阵。

### 2.2 为什么"合成"而非"拆 2 skill"

| 维度 | 拆 2 skill | 合成 1 skill |
|------|-----------|-------------|
| 基础设施 | 需下沉 `em_get`/`@source`/failover | 复用现有 |
| 交叉接口归属 | 每次判断（融资融券/三表归哪边） | 同 skill 内主源+备源 |
| agent 路由层数 | 2 层 | 1 层（直接选 reference） |
| 代码重复 | 等价接口需协调 | 0 |
| progressive disclosure | 一般 | 更好（references 分层是原设计） |

合成方案胜出。**但"9/10 走东财且命中 sgw 现有组"的旧结论不准确**——见 [§3 阶段 2](#阶段-2请求基础设施正确性前置)，akshare 用的若干东财子域（`datacenter.eastmoney.com`、`emweb.securities.eastmoney.com`）当前未在 sgw `config.toml` 精确归组，需逐项核验。板块接口的编号 push2 子域已决策主用无编号 host（[§7 决策7](#7-决策记录)），编号仅作备选。

### 2.3 合成后的 skill 自我描述

`a-stock-data` 涵盖「实时交易时序数据 + 研究型结构性数据」两类，经同一 sgw 网关。研究型接口的**全市场扫描**与**单证券查询**由函数签名明确区分，不伪装成统一的 `code` 参数。

---

## 3. 阶段拆解

每阶段遵循 AGENTS.md §5：**一 commit = 一逻辑变更，代码库始终可工作**。

### 阶段 1：事实冻结与接口契约

- [x] 冻结 AkShare snapshot（见 §0）
- [x] 完成 [§4 interface inventory](#4-authoritative-interface-inventory)，每行确定稳定 ID、签名、host/path/method、分页、tier/via、依赖和验收
- [x] 逐项核验 inventory 的 `upstream_host` 与 `packages/sgw/sgw/config.toml` 端点政策

**交付**：本文档 §4 的 17 个公共函数 inventory，状态均为 `implemented`。

### 阶段 2：请求基础设施正确性前置

> 这些是移植任何东财/同花顺/交易所接口前的**正确性前置**，不是可选增强。详见 [integration-analysis §1](akshare-integration-analysis.md)。

- [x] **sgw host 精确归组**：`emweb`、`datacenter`、无编号 `push2` 与 `www.szse.cn` 均已配置；编号 push2 不做 wildcard
- [x] **请求头白名单透传**：支持 `User-Agent`/`Referer`/`Cookie`/`X-CSRF-Token`/`Accept`，凭据不进入公共缓存身份或日志
- [x] **query-aware canonical cache key**：合并 target query 与 params，排序编码并覆盖跨股票/日期/页码隔离测试
- [x] **`_datacenter` 全量分页**：支持 `all_pages`/`max_pages`、空结果与分页失败边界
- [x] **通用/分源请求客户端边界**：受控源经 sgw；乐咕等已评审安全源直连；未知端点失败关闭
- [x] 上述各项已有 mock 单元测试和 endpoint inventory CI

**交付**：sgw/asgk 基础设施变更 + 单元测试。**本阶段不移植任何 akshare 业务接口。**

### 阶段 3：纯 JSON、低基建接口

只移植 inventory 中满足全部条件的行：host 已可路由、GET、header 已支持、JSON、分页已正确实现、参数契约已确定。

典型候选：十大股东、股东持股变化、股东协同、业绩预告/快报、股权质押、商誉、高管增减持、回购、机构调研、板块成份股。**注意**：其中多数是**全市场/报告期/日期/股东关键词/板块名**接口，不是单股 `code` 查询——按 inventory 行的 `asgk_input` 实现真实签名。

按领域切片提交（一个可独立工作的领域切片 = 一 commit，含实现+测试+reference+`__init__.py` 导出+SKILL.md 路由）。SKILL.md 路由只在能力真正可用时更新。

### 阶段 4：HTML / 签名 / xlsx / 算法接口

按 inventory 的 structured 类行按需实现，对应 [port-feasibility §3](akshare-port-feasibility.md) 的技术模式：
- 筹码分布（CYQ 本地算法）
- 乐咕全市场 PE/PB（JS 版 MD5 token + CSRF）
- 深交所 ShowReport xlsx 流（融资融券官方容灾源、市场总貌等）
- 同花顺签名/HTML 接口（若纳入范围）

按需新增 helper（`_signing.py`/`_htmltable.py`/`_xlsx.py`/`_dataframe.py`），**有 approved inventory 消费者才新增**；vendor JS 写明来源/commit/license/hash；asgk 直接 import 的第三方包显式声明到 `pyproject.toml`。

### 阶段 5：真正的独立源 failover 与按需扩展

把 akshare 的**不同风控面独立源**（交易所官方等）补进现有模块作容灾，写入 `references/failover.md`。注意区分：
- **source failover**：不同 host 的独立源容灾（如交易所官方 vs 东财）——有价值
- **dimension complement**：同源不同维度（如东财个股 + 东财营业部统计）——不是封禁容灾

P2 按需项（不列时间表）：同花顺技术选股、千股千评、雪球/百度舆情、ESG/杜邦、股东大会日历等。

---

## 4. Authoritative interface inventory

> **本节是三份文档中唯一的接口清单**。`integration-analysis.md` 和 `port-feasibility.md` 只引用此处的 ID，不复制表格。
> **行粒度**：一行 = 一个拟新增的 asgk 公共函数。不按"能力包"或上游文件计数。
> **快照**：akshare 1.18.64 / `fcdbf25`（见 §0）。源码路径证据见 `upstream_evidence`。
> **状态字段**：`candidate`（评审中）/ `approved`（评审通过可实施）/ `blocked` / `implemented` / `deferred` / `rejected`。
> **gateway_readiness**：`ready`（host 已归组）/ `host-missing` / `header-missing` / `method-missing`（POST）/ `direct`（决策直连）。

### 4.0 字段说明

| 字段 | 说明 |
|---|---|
| id | 稳定 ID，文档间唯一引用键 |
| status / phase | 状态 / 阶段（见 §3） |
| capability | 用户能力名 |
| asgk_function / target_module / reference_doc | 目标公开函数 / 文件 / reference |
| upstream_function / upstream_evidence | AkShare 蓝本函数 / 源码路径:行号 |
| upstream_host / path / method | 精确上游信息（不写"东财 datacenter"模糊词） |
| request_params | 固定 + 动态参数 |
| required_headers | Referer/Cookie/UA 等；无写 `none` |
| response_kind | JSON/HTML/xlsx/text |
| pagination | none / pages / count / cursor；page 参数名、终止条件、默认/最大页数 |
| upstream_input | AkShare 原始签名及语义 |
| asgk_input | 拟定公开签名（按真实语义） |
| input_mapping | code 前缀、日期格式、板块名→代码等转换 |
| output_type / output_schema | 返回类型 / 稳定字段名+类型+单位+nullable |
| tier / via / gateway_readiness | 缓存档 / gateway 或 direct / 网关就绪状态 |
| dependencies | 直接依赖 + helper |
| existing_overlap | 现有函数及 duplicate/complement/failover 关系 |
| fixture_args | 合法测试样例参数（按接口类型，不统一用 600519/000001） |
| acceptance | 行级验收标准 |
| notes | 其他 |

### 4.1 股东领域（holders.py / holders.md）

| id | AKP-HOLD-001 |
|---|---|
| status / phase | **implemented** (commit fad9ae2) / 3 |
| capability | 十大股东明细 |
| asgk | `top10_holders(symbol, date) -> list[dict]` / holders.py / holders.md |
| upstream | `stock_gdfx_top_10_em(symbol, date)` / `stock_gdfx_em.py:452` |
| host/path/method | `emweb.securities.eastmoney.com` / `/PC_HSF10/ShareholderResearch/PageSDGD` / GET |
| params | symbol + date（拼 YYYY-MM-DD） |
| headers | none |
| response | JSON（取 `sdgd`） |
| pagination | none（单页） |
| upstream_input | `symbol="sh688686"`（**带市场前缀**，内部 `.upper()`）, `date="20210630"`（报告期） |
| asgk_input | `top10_holders(symbol: str, date: str)`；symbol 接受 `sh688686`/`sz000420` |
| input_mapping | asgk 可提供便捷封装 `code="688686"`→按 6/0/3 判定前缀，但须在 reference 标注 |
| output | list[dict] |
| tier/via/readiness | L / gateway / **ready**（`emweb.securities.eastmoney.com` 已归组） |
| deps | `_datacenter` **不适用**（非 datacenter 端点，需直接 `em_get`） |
| overlap | complement 现有 `holder_count`（股东户数） |
| fixture | `("sh688686","20240930")` / `("sh600519","20240930")` |
| acceptance | 指定 symbol+报告期返回 10 条左右；字段含股东名/持股数/比例/性质；非报告期返回空且不报错 |

| id | AKP-HOLD-002 |
|---|---|
| status / phase | **implemented** (commit fad9ae2) / 3 |
| capability | 十大流通股东明细 |
| asgk | `top10_free_holders(symbol, date) -> list[dict]` / holders.py |
| upstream | `stock_gdfx_free_top_10_em(symbol, date)` / `stock_gdfx_em.py:393` |
| host/path/method | `emweb.securities.eastmoney.com` / `/PC_HSF10/ShareholderResearch/PageSDLTGD` / GET |
| params | symbol + date |
| headers | none |
| response | JSON（取 `sdltgd`） |
| pagination | none |
| upstream_input | `symbol="sh688686"`（带前缀）, `date="20240930"` |
| asgk_input | `top10_free_holders(symbol: str, date: str)` |
| tier/via/readiness | L / gateway / **ready**（同 001） |
| deps | `_datacenter` 不适用 |
| fixture | `("sh688686","20240930")` |
| acceptance | 同 001，字段为流通股东维度 |

| id | AKP-HOLD-003 |
|---|---|
| status / phase | **implemented** (commit fad9ae2) / 3 |
| capability | 股东持股变化（全市场统计） |
| asgk | `holder_change(date) -> list[dict]` / holders.py |
| upstream | `stock_gdfx_holding_change_em(date)` / `stock_gdfx_em.py:313` |
| host/path/method | `datacenter-web.eastmoney.com` / `/api/data/v1/get` / GET |
| params | reportName=`RPT_HOLDERS_BASIC_INFO`, filter=`END_DATE`, pageNumber/pageSize |
| headers | none |
| response | JSON（`result.data`） |
| pagination | **pages**（`result.pages`，遍历 1..total） |
| upstream_input | `date="20210930"`（**报告期，无股票代码**） |
| asgk_input | `holder_change(date: str)`；如提供 `code` 过滤，须标明是上游 filter 还是本地过滤 |
| tier/via/readiness | L / gateway / **ready**（datacenter-web + 全量分页） |
| deps | `_datacenter`（需 all_pages） |
| fixture | `("20240930",)` |
| acceptance | 返回全市场多页记录；末页正确；总记录数 = sum(pages) |

| id | AKP-HOLD-004 |
|---|---|
| status / phase | **implemented** (commit fad9ae2) / 3 |
| capability | 股东协同（按股东类型） |
| asgk | `holder_teamwork(holder_type="全部") -> list[dict]` / holders.py |
| upstream | `stock_gdfx_holding_teamwork_em(symbol)` / `stock_gdfx_em.py:953` |
| host/path/method | `datacenter-web.eastmoney.com` / `/api/data/v1/get` / GET |
| params | reportName, filter=`HOLDER_TYPE`（仅非"全部"时）, pageNumber/pageSize |
| headers | none |
| response | JSON |
| pagination | pages |
| upstream_input | `symbol="社保"`（**股东类型关键词**，非股票代码；取值：全部/个人/基金/QFII/社保/券商/信托） |
| asgk_input | `holder_teamwork(holder_type: str = "全部")`；参数名用 `holder_type` 避免与股票代码混淆 |
| input_mapping | 校验 holder_type ∈ 枚举 |
| tier/via/readiness | L / gateway / 同 003 |
| deps | `_datacenter`（需 all_pages） |
| fixture | `("社保",)` / `("基金",)` |
| acceptance | 不同 holder_type 返回不同记录集；"全部"返回最多 |

### 4.2 业绩领域（earning.py / earning.md）

| id | AKP-EARN-001 / 002 |
|---|---|
| status / phase | **implemented** (commit 38bda00) / 3 |
| capability | 业绩预告 / 业绩快报（全市场报告期） |
| asgk | `earning_forecast(date)` / `earning_express(date)` / earning.py |
| upstream | `stock_yjyg_em(date)` / `stock_yjyg_em.py:135` ; `stock_yjkb_em(date)` / `stock_yjyg_em.py:17` |
| host/path/method | `datacenter-web.eastmoney.com` / `/api/data/v1/get` / GET（**真机验证：业绩 reportName 在 datacenter-web 可用，复用 `_datacenter()`，§7 决策11**） |
| params | reportName（`RPT_PUBLIC_OP_NEWPREDICT` / `RPT_FCI_PERFORMANCEE`）, filter=`(REPORT_DATE='YYYY-MM-DD')`（等值匹配）, source=WEB, pageNumber/pageSize |
| headers | none |
| response | JSON |
| pagination | pages |
| upstream_input | `date="20200331"` / `"20211231"`（**报告期，无股票代码**） |
| asgk_input | `earning_forecast(date: str)` / `earning_express(date: str)` |
| tier/via/readiness | L / gateway / **ready**（host 已归组 Commit C，分页已实现 Commit D） |
| deps | 复用 `_datacenter(all_pages=True)`（Commit D） |
| fixture | `("20240930",)` |
| acceptance | 报告期有数据返回多页；非报告期返回空且不报错；报告名映射正确 |
| notes | filter 语法是等值 `(REPORT_DATE='2024-09-30')`（akshare 实际用法），**非**前缀 `^"..."`（早先描述有误，真机验证修正） |

### 4.3 筹码领域（chip.py / chip.md）

| id | AKP-CHIP-001 |
|---|---|
| status / phase | **implemented** (commit f9f679b) / 4 |
| capability | 筹码分布 + 主力成本 |
| asgk | `chip_distribution(symbol, adjust="") -> list[dict]` / chip.py |
| upstream | `stock_cyq_em(symbol, adjust)` / `stock_cyq_em.py:16` |
| host/path/method | `push2his.eastmoney.com` / `/api/qt/stock/kline/get` / GET（取近 210 根 K 线） |
| params | secid（由 symbol 拼出）, lmt=210, adjust |
| headers | none |
| response | JSON K 线 → **本地 JS 计算 CYQ**（`py_mini_racer` 执行 `CYQCalculator`，返回最近 90 行） |
| pagination | none（单次 K 线请求） |
| upstream_input | `symbol="000001"`（**纯数字不带前缀**，市场由首字符 6 判定）, `adjust ∈ {"","qfq","hfq"}` |
| asgk_input | `chip_distribution(symbol: str, adjust: str = "")` |
| input_mapping | symbol 纯数字；secid = `1.{symbol}` 若 6 开头 else `0.{symbol}` |
| tier/via/readiness | S / gateway / **ready**（push2his 已归组） |
| deps | `py-mini-racer`（已声明直接依赖）+ vendor CYQ JS（来源/commit/license/hash）。**使用 vendor JS（方案 A），不 Python 重写** |
| overlap | complement 现有 K 线接口（百度/mootdx），筹码是独有维度 |
| fixture | `("000001",)` / `("600519",)` |
| acceptance | 返回 ~90 行筹码分布；不同 adjust 返回不同成本；CYQ JS 线程安全（阶段4并发测试，thread-local/锁） |
| notes | CYQ 是**业务算法**（非响应解密），纯数学零 DOM 依赖（`stock_cyq_em.py:27-218`）。决策选 vendor JS + py_mini_racer（与上游完全一致，上游改了只换文件）。py_mini_racer 并发安全须阶段4 测试 + thread-local/锁保护 |

### 4.4 板块领域（board.py / board.md）

| id | AKP-BOARD-001 / 002 |
|---|---|
| status / phase | **implemented** (commit c3b6a70) / 3 |
| capability | 概念板块 / 行业板块成份股 |
| asgk | `board_constituents(symbol, kind) -> list[dict]` / board.py（一个函数用 kind 区分概念/行业，一行） |
| upstream | `stock_board_concept_cons_em(symbol)` / `stock_board_concept_em.py:428` ; `stock_board_industry_cons_em(symbol)` / `stock_board_industry_em.py:461` |
| host/path/method | `push2.eastmoney.com` / `/api/qt/clist/get` / GET（**主用无编号 canonical host**，asgk 现有 5 处 push2 调用均验证可用） |
| params | pn/pz=100, fid, ut=bd1d9ddb..., fields |
| headers | none |
| response | JSON（`data.diff`） |
| pagination | **count**（经 `fetch_paginated_data`，pn 递增，sleep 0.5-1.5s） |
| upstream_input | `symbol="融资融券"` / `"小金属"`（**板块名称或代码 BK0655/BK1027**） |
| asgk_input | `board_constituents(symbol: str, kind: str = "concept")` |
| input_mapping | 名称→板块代码（内部辅助请求）；kind ∈ {concept, industry} |
| tier/via/readiness | S / gateway / **ready**（无编号 push2.eastmoney.com 已归组） |
| deps | 通用 push2 helper |
| failover | **编号子域作备选**：无编号 host 失败时降级到 akshare 蓝本的编号 host（`29.push2`/`79.push2` 等），需在 sgw 归组或直连降级路径中处理 |
| overlap | complement 现有 `em_hot_concept`（个股→概念），此处是反向（板块→成份股） |
| fixture | `("融资融券","concept")` / `("小金属","industry")` |
| acceptance | 稳定板块名返回多页成份股；分页完整；名称与代码两种输入均可；无编号 host 失败时备选编号 host 可降级 |
| notes | push2 clist 分页参数是 `pn`（非 `pageNumber`），与 datacenter 不同。akshare 蓝本用编号 host 是因东财前端服务器分配，asgk 统一无编号便于网关归组 |

### 4.5 事件领域（risk_event.py / risk_event.md）

| id | AKP-EVT-001 |
|---|---|
| status / phase | **implemented** (commit db15730) / 3 |
| capability | 高管增减持明细（全市场，无参数） |
| asgk | `mgmt_trade() -> list[dict]` / risk_event.py |
| upstream | `stock_hold_management_detail_em()` / `stock_hold_control_em.py:14` |
| host/path/method | `datacenter-web.eastmoney.com` / `/api/data/v1/get` / GET |
| params | reportName=`RPT_EXECUTIVE_HOLD_DETAILS`, pageNumber/pageSize=5000（注：上游冗余传 p/pageNo/pageNum 但生效的是 pageNumber） |
| headers | none |
| response | JSON |
| pagination | pages |
| upstream_input | **无参数** |
| asgk_input | `mgmt_trade()`（如需 code 过滤，标明本地过滤 + 时间窗口） |
| tier/via/readiness | S / gateway / **ready**（全量分页已实现） |
| deps | `_datacenter`（需 all_pages + 大 pageSize） |
| fixture | 无参；验收按 schema |
| acceptance | 返回全市场多页；schema 稳定；不要求特定股票出现 |

| id | AKP-EVT-002 |
|---|---|
| status / phase | **implemented** (commit db15730) / 3 |
| capability | 股票回购（全市场，无参数） |
| asgk | `repurchase() -> list[dict]` / risk_event.py |
| upstream | `stock_repurchase_em()` / `stock_repurchase_em.py:14` |
| host/path/method | `datacenter-web.eastmoney.com` / `/api/data/v1/get` / GET |
| params | reportName=`RPTA_WEB_GETHGLIST_NEW`, pageNumber/pageSize=500 |
| headers | none |
| response | JSON |
| pagination | pages |
| upstream_input | **无参数** |
| asgk_input | `repurchase()` |
| tier/via/readiness | **S**（进行中回购状态会更新，**不应 P 档**）/ gateway / 同 001 |
| deps | `_datacenter`（all_pages） |
| fixture | 无参 |
| acceptance | 全市场多页；进行中状态字段可变 |

| id | AKP-EVT-003 |
|---|---|
| status / phase | **implemented** (commit db15730) / 3 |
| capability | 机构调研（全市场，按开始日期） |
| asgk | `institute_research(date) -> list[dict]` / risk_event.py |
| upstream | `stock_jgdy_detail_em(date)` / `stock_jgdy_em.py:108` |
| host/path/method | `datacenter-web.eastmoney.com` / `/api/data/v1/get` / GET |
| params | reportName=`RPT_ORG_SURVEY`, filter=`RECEIVE_START_DATE`, pageNumber/**pageSize=50** |
| headers | none |
| response | JSON |
| pagination | pages（pageSize=50，注意较小） |
| upstream_input | `date="20241211"`（**开始时间，无股票代码**） |
| asgk_input | `institute_research(date: str)` |
| tier/via/readiness | S / gateway / 同上 |
| deps | `_datacenter`（all_pages，注意 pageSize=50 页数多） |
| fixture | `("20241201",)` |
| acceptance | 按日期返回多页；page_size 正确 |

### 4.6 风险领域（pool_filter.py / pool_filter.md）

| id | AKP-RISK-001 |
|---|---|
| status / phase | **implemented** (commit e2ae92c) / 3 |
| capability | 股权质押比例（全市场，按交易日） |
| asgk | `pledge_ratio(date) -> list[dict]` / pool_filter.py |
| upstream | `stock_gpzy_pledge_ratio_em(date)` / `stock_gpzy_em.py:88` |
| host/path/method | `datacenter-web.eastmoney.com` / `/api/data/v1/get` / GET |
| params | reportName=`RPT_CSDC_LIST`, filter=`TRADE_DATE`, pageNumber/pageSize=500 |
| headers | none |
| response | JSON |
| pagination | pages |
| upstream_input | `date="20240906"`（**交易日，无股票代码**） |
| asgk_input | `pledge_ratio(date: str)` |
| tier/via/readiness | S / gateway / 同上 |
| deps | `_datacenter`（all_pages） |
| fixture | `("20240906",)` |
| acceptance | 按交易日全市场多页 |

| id | AKP-RISK-002 |
|---|---|
| status / phase | **implemented** (commit e2ae92c) / 3 |
| capability | 商誉明细（全市场，按报告期） |
| asgk | `goodwill(date) -> list[dict]` / pool_filter.py |
| upstream | `stock_sy_em(date)` / `stock_sy_em.py:294` |
| host/path/method | `datacenter-web.eastmoney.com` / `/api/data/v1/get` / GET |
| params | reportName=`RPT_GOODWILL_STOCKDETAILS`, filter=`REPORT_DATE`, pageNumber/pageSize=5000, **token=894050c76...（硬编码固定值）** |
| headers | none |
| response | JSON |
| pagination | pages |
| upstream_input | `date="20231231"`（**报告期**） |
| asgk_input | `goodwill(date: str)` |
| tier/via/readiness | L / gateway / 同上 |
| deps | `_datacenter`（all_pages）；固定 token 作为常量 |
| fixture | `("20231231",)` |
| acceptance | 报告期多页；固定 token 生效 |
| notes | token 是**硬编码固定值**（非动态签名），作为模块常量即可 |

### 4.7 估值领域（valuation_hist.py / valuation_hist.md）

| id | AKP-VAL-001 / 002 |
|---|---|
| status / phase | **implemented** (commit 328a4e1) / 4 |
| capability | 全市场 PE / PB 历史（乐咕） |
| asgk | `market_pe_lg(market) / market_pb_lg(market) -> list[dict]` / valuation_hist.py |
| upstream | `stock_market_pe_lg(symbol)` / `stock_a_pe_and_pb.py:322` ; `stock_market_pb_lg` / `:463` |
| host/path/method | `legulegu.com` / `/api/stock-data/market-pe`（PB 分支 `/market-pb`，科创版分支 `/api/stockdata/get-ke-chuang-ban-pe`） / GET |
| params | token + symbol |
| headers | **Cookie + `X-CSRF-Token`**（经 `get_cookie_csrf`） |
| response | JSON |
| pagination | none |
| upstream_input | `symbol="深证"`（**市场关键词**：上证/深证/创业板/科创版） |
| asgk_input | `market_pe_lg(market: str)` / `market_pb_lg(market: str)` |
| tier/via/readiness | L / **direct（已验证，见 §7）** / direct |
| deps | token = **JS 版 MD5**（内联 `hash_code`，py_mini_racer 执行 `hex(date)`）+ CSRF cookie；`py-mini-racer` 直接依赖 |
| fixture | `("深证",)` / `("上证",)` |
| acceptance | 返回时间序列；token 与 CSRF 双重生效 |
| notes | snapshot 中**无个股 PE/PB 接口**（`stock_a_indicator_lg` 不存在）；个股估值已由现有 `valuation` 层覆盖，不移植。token 算法见 [port-feasibility §2.3](akshare-port-feasibility.md) |

### 4.8 failover 领域（扩 failover.md）

| id | AKP-FAILOVER-001 |
|---|---|
| status / phase | **implemented** (commit cbd3e21) / 5 |
| capability | 深交所融资融券明细（官方容灾源） |
| asgk | `margin_detail_szse(date) -> list[dict]`（`capital.py`） |
| upstream | `stock_margin_detail_szse(date)` / `stock_margin_szse.py:93` |
| host/path/method | **`www.szse.cn`** / `/api/report/ShowReport` / GET |
| params | SHOWTYPE=xlsx, CATALOGID=1837_xxpl, TABKEY=tab2, tab2PAGENO=1 |
| headers | **`Referer: https://www.szse.cn/disclosure/margin/margin/index.html` + UA** |
| response | **xlsx**（bytes） |
| pagination | none（单页，上游未遍历多页） |
| upstream_input | `date="20230925"`（交易日） |
| asgk_input | `margin_detail_szse(date: str)` |
| input_mapping | date→YYYY-MM-DD |
| tier/via/readiness | S / **gateway（见 §7）** / **ready**（exchange 组 + Referer 白名单） |
| deps | `_xlsx.py` + `openpyxl`（已声明直接依赖） |
| overlap | **failover** 现有 `margin_trading`（东财主源）——东财被封时官方兜底 |
| fixture | `("20230925",)` |
| acceptance | 返回 xlsx 解析为 list[dict]；证券代码保前导零（dtype=str）；Referer 透传到上游 |

> 其余 ShowReport 系列（市场总貌/标的名单/汇率/代码表）结构相同，实施时按同模式批量处理并各自占 inventory 一行。

### 4.9 统计

> 由本表重算，禁止再用"P0 N 接口"口径。inventory 共 14 个 id 块，其中 EARN/BOARD/VAL 各为 2 函数合并块，展开为 17 个候选公共函数。

- **候选 asgk 公共函数**：17（HOLD 4 + EARN 2 + CHIP 1 + BOARD 2 + EVT 3 + RISK 2 + VAL 2 + FAILOVER 1）
- **unique upstream 蓝本函数**：18（候选函数 17 + BOARD 的 `board_constituents(kind=)` 一个 asgk 函数对应概念/行业 2 个上游函数，多出 1 个）
- **unique source hosts（主用）**：`emweb.securities.eastmoney.com`、`datacenter-web.eastmoney.com`（EARN 已验证可复用，§7 决策11）、`push2his.eastmoney.com`、`push2.eastmoney.com`（BOARD 主用无编号，编号 `29./79./91.` 作备选）、`legulegu.com`、`www.szse.cn`（共 6 个主用 host + datacenter.eastmoney.com 备选）
- **阶段分布**：阶段3（JSON）13 函数（HOLD 4 + EARN 2 + BOARD 2 + EVT 3 + RISK 2）；阶段4（structured）3 函数（CHIP 1 vendor JS + VAL 2）；阶段5（failover）1 函数（FAILOVER）
- **gateway_readiness 分布**：所有 gateway 接口均为 ready；EARN 复用 datacenter-web + all_pages，BOARD 使用已归组的无编号 host，FAILOVER 使用 exchange 组并透传 Referer；乐咕按 §7 决策10 直连

---

## 5. 交叉接口关系矩阵（替换旧"归属表"）

合成后无需决策"归哪个 skill"，只标注关系：

| 能力 | 主源（现有） | akshare 移植（备/补） | 关系 |
|------|------|---------------------|------|
| 龙虎榜 | 东财个股+全市场 | （东财营业部统计若移植） | dimension complement（同源不同维度，非封禁容灾） |
| 融资融券 | 东财明细 | 深交所官方（AKP-FAILOVER-001） | **source failover**（独立源，东财被封可兜底） |
| 财报三表 | 新浪 | （东财三表若移植） | source failover |
| 板块 | 个股→概念命中（现有） | 板块→成份股（AKP-BOARD-001） | **direction complement**（反向查询） |
| 股东户数 | 东财变化（现有） | 十大股东明细（AKP-HOLD-001/002） | dimension complement |
| 一致预期 EPS | 同花顺（现有） | （东财预测若移植） | source failover |
| 个股估值 | PEG/前向PE（现有本地计算） | — | akshare 无个股 PE/PB，不重复 |

---

## 6. 风险与缓解

| 风险 | 严重度 | 缓解 |
|------|-------|------|
| **sgw 后缀通过但 exact-host 未归组**，接口静默 400 | 高 | 阶段2 逐 host 核验 config；板块接口主用无编号 host（已验证可用），降低归组复杂度 |
| **sgw 不透传 Referer/Cookie/CSRF**，交易所/同花顺接口失效 | 高 | 阶段2 设计 header 白名单 + 测试 |
| **cache key 忽略 params，不同股票/页串缓存** | 高 | 阶段2 canonical prepared URL；测试 600519≠000001 |
| **`_datacenter` 只取第一页**，全市场接口静默截断 | 高 | 阶段2 all_pages + 末页验收 |
| **乐咕/交易所真机风控** | 中 | §7 决策10 已保守验证（单发间隔10s 无封禁）；生产加压需小步观察，非直接放开 |
| akshare 端点字段漂移（snapshot vs 现网） | 中 | 每接口按 fixture 真机验证，字段缺失记入 reference"已知限制"；不追上游 |
| 传递依赖（pandas/mini-racer 经 mootdx）未来消失 | 中 | asgk 直接 import 的包显式声明到 pyproject |
| snapshot 中接口已不存在（如 `stock_a_indicator_lg`） | 中 | 已核验并在 inventory 标注；上游同步时持续复核 |
| vendor JS 许可证/来源 | 低 | submodule 保留上游 MIT LICENSE；vendor 文件加来源/commit/hash（§7 决策6/9） |
| mini-racer 并发非线程安全 | 中 | 阶段4 CYQ 用 thread-local context/锁 + 并发测试 |
| 无编号 push2 host 失败 | 低 | 编号子域作降级备选（§7 决策7） |
| SKILL.md 路由表膨胀 | 低 | 二级分组 + reference 分层吸收 |

---

## 7. 决策记录

> 散落三文档的决策点集中于此；决策和实施验证均已完成。

### 已裁决
1. **不新增 skill**：合成进 a-stock-data（§2.2）。
2. **不 `pip install akshare`**：作 ref 蓝本移植（AGENTS.md §6）。
3. **interface inventory 唯一存放本文件**：另两文档只引用 ID。
4. **`@source sign/parse` 字段不扩展**：当前无明确消费者（无 registry 导出/文档生成器）。`@source` 只是声明元数据，**不驱动缓存 tier**——实际 tier 仍由函数体内 `em_get(..., tier=...)` 决定，验收须同时校验装饰器声明与运行时 `X-Cache-Tier` 一致。
5. **POST 接口不预先扩展 sgw**：当前候选全是 GET，P2 按需。
6. **akshare 仓库处置 = submodule**：借鉴的 repo 作 `ref/akshare` submodule（与 `ref/a-stock-data` 同等地位，符合 AGENTS.md §6 ref 语义）。`.agents/temp/akshare` 仅探索用，submodule 落定后删除。固定 commit `fcdbf25`，不追上游。
7. **编号 push2 host 策略 = 主用无编号 + 编号备选**：inventory 板块接口主用无编号 canonical `push2.eastmoney.com`（asgk 现有 5 处验证可用）；akshare 蓝本的编号 host（`29./79./91.`）作**失败降级备选**。sgw 不需为编号 host 做 wildcard 归组，降级路径另处理。
8. **CYQ 实现 = vendor JS（方案 A）**：vendor akshare 的 CYQ JS 用 py_mini_racer 执行，**不 Python 重写**。理由：与上游完全一致；CYQ 是纯数学零 DOM（`stock_cyq_em.py:27-218`），py_mini_racer 可直接跑。代价：py_mini_racer 须显式声明直接依赖 + 阶段4 并发安全测试（thread-local/锁）。
9. **vendor JS 同步 = CI 定期 diff（方案 B）**：锁定 snapshot，CI 定期 diff 上游 ths.js/CYQ JS，变更告警人工更新。当前 inventory 范围（17 候选）只用 CYQ；ths.js 在 P2 同花顺接口才需要，该项推迟到 P2 触发时实施。

### 已验证（真机确认，2026-07-31）

> 两项 go/no-go spike 已用保守策略（单发、间隔 ≥10s）真机验证完成，无封禁。结论如下。

10. **乐咕/交易所风控验证 → 通过（无风控迹象）**：
    - **深交所 szse**：3 次请求（间隔 10s）ShowReport 端点（带 Referer+UA），均 HTTP 200，稳定返回 ~130KB xlsx。无明显风控。
    - **乐咕 legulegu**：2 次请求（间隔 10s）巴菲特指标 API（token=md5(日期) + CSRF cookie + X-CSRF-Token），均 HTTP 200，稳定返回 388KB / 5176 条。无明显风控。
    - **裁决**：两源确认可通过保守策略访问。乐咕**直连** + 自律限流（复用 `_direct_throttle`，保守参数如 1 req/10s）；深交所经**网关 exchange 独立组**（与东财不同风控面，共享缓存有价值，Referer 经 Commit B header 白名单透传）。
    - **限制**：本次为低频单发验证，未测高频/并发场景。生产部署若需更高频，应先小步加压观察，而非直接放开。
    - **AKP-FAILOVER-001（深交所融资融券）可行性确认**：Referer 已透传，`www.szse.cn` 已归入 exchange 组，并由非空 XLSX fixture 验证字段映射。

11. **`datacenter.eastmoney.com` vs `datacenter-web` reportName 互通性 → 互通，复用 `_datacenter()`**：
    - **真机验证**：`datacenter-web.eastmoney.com/api/data/v1/get` + `reportName=RPT_PUBLIC_OP_NEWPREDICT` + `filter=(REPORT_DATE='2024-09-30')` + `source=WEB` → **success: True, 200 页**，与 securities 端点结果一致。
    - **裁决**：**AKP-EARN 直接复用 `_datacenter()`**，host 用 datacenter-web（已在 eastmoney 组），不需新增 securities helper。
    - **关键修正**：filter 语法是等值 `(REPORT_DATE='2024-09-30')`（akshare 实际用法），非前缀匹配 `^"..."`（早先 review 描述有误）。inventory AKP-EARN 的 host 字段从 `datacenter.eastmoney.com` 改为 `datacenter-web.eastmoney.com`，deps 改为"复用 `_datacenter(all_pages=True)`"。
    - **附带结论**：Commit C 把 `datacenter.eastmoney.com` 加入了 eastmoney 组，虽不再被 AKP-EARN 需要（改用 datacenter-web），但保留无害，可作为 securities 端点的备选归组。

---

## 8. 不在本计划范围

- ❌ akshare 港股/美股/期货/期权（非 ETF）/外汇/加密/基金/债券接口
- ❌ 蛋卷基金/宏观中国/经济数据等非 A 股方向
- ❌ 拆分为多个 skill
- ❌ 追 akshare 上游新版本（锁 snapshot `fcdbf25`）
- ❌ 个股 PE/PB（snapshot 无此接口，且现有 `valuation` 已覆盖本地计算）
- ❌ **保持 sgw/_datacenter 现状**——阶段2 的基础设施修正是**范围内**的（旧文档误列为"范围外"）

---

## 9. akshare 仓库处置（submodule）

**决策（§7 决策6）**：作 `ref/akshare` submodule，与 `ref/a-stock-data` 并列。

理由：
- AGENTS.md §6 的 ref 语义是"只读对照，不直接用于生产"。借鉴的 repo 作 submodule 符合此语义，与 `ref/a-stock-data`（同为改造蓝本）形式一致。
- submodule 固定 commit `fcdbf25`，不追上游频繁更新。
- LICENSE 为 MIT（版权 Albert King 2019-2026），submodule 保留上游完整 LICENSE，vendor 出来的 JS 文件另加来源/commit/hash 注释。

**落地动作**：
- [x] 添加 `ref/akshare` submodule
- [x] 固定到 `fcdbf25aa864a218c54864c3f6ab6a2ed19cce28`
- [x] 提交 `.gitmodules` + submodule 指针
- [x] 删除 `.agents/temp/akshare` 探索目录

**vendor 出来的文件**（如 `asgk/_vendor/cyq.js`、`asgk/_vendor/ths.js`）是 submodule 内容的**子集拷贝**，每个文件头注明：来源路径、snapshot commit、LICENSE 归属、local hash、同步策略（§7 决策9 CI diff）。

---

## 10. 完成状态

1. §4 inventory 17/17 均为 `implemented`。
2. AkShare submodule、基础设施前置、JSON/structured/failover 接口均已落地。
3. macOS ARM64 与 Linux x86_64 完整离线回归通过；真实来源只做受预算约束的串行 canary。
4. 10 个 pi agent 功能矩阵、1000 并发 mock 边界和 SZSE 非空 fixture 已记录在 [testing.md](testing.md)。
