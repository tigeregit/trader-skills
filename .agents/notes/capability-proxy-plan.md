# 能力代理重构 — 实施 task list

本文件把 `capability-proxy-design.md` 转化为可执行的 commit 级任务列表。
**每个任务 = 一个原子 commit**，单一逻辑理由，主分支全程可用。

> **分支**：`refactor/capability-proxy`（设计文档已在此分支）
> **原则**：业务函数零改动（em_get 内部切换为枢纽）；每任务独立测试 + 提交；
> 任务间有依赖时严格按序，无依赖的可并行。
> **验收通用标准**：sgw + asgk 全套测试绿；已迁移能力盘中实测返回真实数据；
> 未配服务端时 fail-closed 或回退旧路径。

## 依赖图

```
T1(骨架+流量内核) ──┬─→ T1.5(cache refactor) ──┬─→ T2(quote试点) ──→ T6~T10
                    │                          ├─→ T3(datacenter族)
                    └─→ T4(客户端格式化) ──────┴─→ T5(CLI)
T6~T10 各梯队迁移完成后 ──→ T11(废弃sgw) ──→ T12(文档收尾)
```

T1 是所有后续任务的地基；T1.5(cache refactor)是 T1 后最优先的（cache 改动最大，
独立验证）；T4/T5 与 T2/T3 并行；T6~T10 按难度梯队串行；T11/T12 最后。

---

## T1 — 建服务端骨架 + 搬入 sgw 流量内核

**目标**：新建 `packages/asgk-server/`，搬入 sgw 的四大流量基础设施，搭出
能力注册框架 + RPC 入口（HTTP JSON）。本任务不含任何业务能力，只搭骨架。

**改动**：
- 新建 `packages/asgk-server/pyproject.toml`（uv workspace 成员）
- 新建 `packages/asgk-server/asgk_server/__init__.py`
- 新建 `packages/asgk-server/asgk_server/traffic.py`：从 sgw/proxy.py 搬入流量内核：
  - **零改搬入**：TokenBucket / SingleFlight / CircuitBreaker / CircuitStateStore /
    CircuitStateManager（含安全闩）/ retry 骨架
  - **cache 部分见 T1.5 单独改造**（Cache/DiskCache 存储内容+key+分档都要改，
    不在本任务做，本任务先占位用最简内存 cache）
- 新建 `packages/asgk-server/asgk_server/registry.py`：`@capability` 装饰器 +
  CapabilityMeta（name/domain/sources/default_source/fallback/data_type/
  supported_formats/cache_policy）+ 注册表
- 新建 `packages/asgk-server/asgk_server/egress.py`：从 sgw 搬入 `_egress_request`
  （requests/curl_cffi 双客户端）
- 新建 `packages/asgk-server/asgk_server/server.py`：HTTP JSON RPC 入口
 （ThreadingHTTPServer，`POST /v1/<capability>` + `GET /v1/sources`），路由到
  注册的能力；流量内核中间件（限流→熔断→出网→缓存占位）
- 新建 `packages/asgk-server/asgk_server/config.toml`：从 sgw/config.toml 搬入
  限流组（group 段原样）；**去掉**端点策略（endpoint 段，改由能力注册表驱动）
- 新建 `packages/asgk-server/tests/test_traffic.py` + `test_registry.py` +
  `test_server.py`（mock 上游，验限流/熔断/能力注册/RPC）

**验收**：
- ✅ 服务端能启动（`asgk-server --port 7701`），`GET /v1/sources` 返回空列表
- ✅ 流量内核测试全绿（限流/熔断/singleflight 从 sgw 测试迁移并适配，47 passed）
- ✅ 注册一个 mock 能力（非真实数据源），`POST /v1/mock_cap` 返回预期结构

**状态**：✅ 已完成（commit 待提交）

**依赖**：无（地基）

---

## T1.5 — cache 机制 refactor（存储内容 + key + 分档）

**目标**：把 sgw 的 Cache/DiskCache 改造为能力代理的 cache（§3.6 全部内容）。
这是流量内核搬入时**改动最大**的部分，独立 commit + 独立验收。

**改动**：
- `asgk_server/cache.py`（新建，从 sgw Cache 改造）：
  - **存储内容**：从 `r.content`（原始字节）改为 fetch 返回的结构化数据
    （dict/list，JSON 序列化存入）
  - **cache key**：从 `tier|canonical_url` 改为 `capability|source|semantic_key`
    （§3.6b/f）。新增 `_semantic_key(capability, source, params)` 取代
    `_canonical_url`：语义参数排序+去重+哈希，不含 source/format/output
  - **per-source 独立**：同 capability 不同 source 各自缓存，不跨源共享（§3.6b）
  - **磁盘持久化改用 JSON 文件**（不沿用 sgw 的 SQLite DiskCache）：每缓存项
    一文件 `<cache_dir>/<capability>/<source>/<param_hash>.json`，含
    `{value, expire}`；SQLite 对 cache 过度工程（无查询需求 + cache 可重建
    不需 ACID + 实测仅6条597KB），见设计 §3.6d。零依赖纯标准库。
  - 熔断状态库（CircuitStateStore）仍用 SQLite（安全闩要 ACID，不换）
- `asgk_server/registry.py`：CapabilityMeta 加 `cache_policy` 字段
  （definitive/quarterly/daily_settled/daily_volatile/realtime/streaming，
  §3.6c 六类）
- `asgk_server/cache_policy.py`（新建）：六类数据类型 → TTL/存储/落盘/粒度映射表
  （§3.6c 表格），取代 sgw 的五档 TTL 一刀切
- `asgk_server/server.py`：接入改造后的 cache（命中返结构化数据；TTL=0 仍走
  singleflight 合并）
- 测试 `test_cache.py`：
  - per-source 缓存隔离（tencent/sina 同参数各一份，不互窜）
  - 六类分档 TTL 正确（定稿30天/季度1天/实时0...）
  - 磁盘持久化（定稿+季度落盘，重启恢复；实时/流式不落盘）
  - singleflight 对 realtime(TTL=0) 仍合并并发
  - 格式不进 key（同数据不同 format 命中同一缓存——此项在 T4 格式化落地后验证）

**验收**：
- ✅ 六类数据类型的 TTL/落盘行为符合 §3.6c 表格（test_cache.py TestCachePolicy）
- ✅ per-source 缓存隔离正确（指定 source 不返回其他源的缓存，实测 tencent/sina 各自 MISS）
- ✅ mock 能力：定稿型落盘后重启仍命中（实测 loaded 1 entry → HIT-MEM）；实时型 no-cache
  但并发合并（test_server.py test_concurrent_miss_coalesced）
- ✅ 缓存测试全绿（35 个新 cache 测试；sgw 101/asgk 138 无回归）

**状态**：✅ 已完成（commit 待提交）

**依赖**：T1

---

## T2 — 第一个真实能力：quote（验证端到端闭环）

**目标**：实现 `quote` 能力（腾讯实时行情），打通"客户端→服务端→上游→结构化返回"
全链路。验证能力代理模式可行。

**改动**：
- `asgk_server/capabilities/quote.py`（新建）：`@capability` 注册 quote，内部
  从 asgk/quote.py 搬入腾讯 URL + GBK 解码 + 53 字段映射（这些知识下沉到此）
- asgk 客户端：在 `em_proxy.py` 加路由——对已注册能力走服务端，其余走旧 sgw
  路径。具体：`em_get` 内部判断 URL 是否属于已下沉能力（用一个 `_SUNK_CAPABILITIES`
  集合驱动），是则调服务端语义接口，否则维持现状
- `asgk_server/tests/test_quote.py`（mock 腾讯上游）

**验收**：
- ✅ `tencent_quote(['600519'])` 经服务端返回真实数据（实测 贵州茅台 price=1308.55
  pe_ttm=19.78 pb=7.02，与旧路径一致）
- ✅ `/v1/sources?capability=quote` 返回 `["tencent"]`
- ✅ 未启动服务端时，quote 回退旧 sgw 路径（test_fallback_to_gateway_when_server_unset）
- ✅ asgk 测试全绿（142 passed，含 4 个新增 tencent 路由测试）

**状态**：✅ 已完成（commit 待提交）

**实施备注**：
- 用 `_server_call(capability, params)` 取代 plan 中的 `_SUNK_CAPABILITIES` URL 路由——
  tencent_quote 有自定义 GBK+53 字段解析，em_get 返回 Response 会让解析崩溃，故
  tencent_quote 直接调语义接口（返回结构化 dict），em_get URL 路由留给后续无需
  自定义解析的批次（datacenter 族）。
- 抽 context.py（FetchContext/SourceBlocked/SourceUnhealthy）破 server↔capabilities 循环导入。

**依赖**：T1

---

## T3 — 零成本梯队：datacenter 族（13 个能力）

**目标**：把东财 datacenter 的 13 个函数（capital/earning/risk_event/pool_filter/
holders/signal 里用 `_datacenter(reportName, filter)` 的）整体下沉。

**改动**：
- `asgk_server/capabilities/datacenter.py`（新建）：一个统一的东财 datacenter
  适配器 + 13 个能力的 reportName/字段映射表（从 asgk 各模块提取）
- asgk 客户端：13 个业务函数的 em_get 调用切到服务端（em_get 路由扩展）
- 测试：13 个能力的 mock 测试

**13 个能力清单**（从调研报告）：
margin_trading / block_trade / holder_num_change / dividend_history /
earning_forecast / earning_express / mgmt_trade / repurchase /
institute_research / pledge_ratio / holder_change / holder_teamwork /
dragon_tiger_board / lockup_expiry / daily_dragon_tiger

**验收**：
- ✅ datacenter 能力经服务端返回真实数据（实测 margin_trading('600519') 融资余额 175亿）
- ✅ 全套测试绿（server 93 / sgw 101 / client 146）

**状态**：✅ 已完成（commit 待提交）

**实施备注**（设计调整）：
- **不做 13 个独立能力**，改为**一个 datacenter 能力**供 15 个业务函数共用。
  原因：15 个函数的查询机制完全相同（同 URL/参数构造/分页），只 reportName/filter/sort
  不同——这些是参数不是知识。字段映射（_s/日期切片/进度码表）是纯计算，按 §6.3 留客户端。
  一能力共用消除冗余，迁移成本真正归零（只改 _datacenter.py 一处）。
- **source 参数重命名 dc_source**：能力的选源控制参数叫 source（数据源 eastmoney），
  东财端点的 source 字段（WEB/HSF10）同名冲突。服务端能力签名用 dc_source，客户端
  _datacenter.py 透明映射（source=WEB → dc_source=WEB）。

**依赖**：T1（可与 T2 并行，但建议 T2 先验证模式）

---

## T4 — 客户端格式化层（_format + _output）

**目标**：实现 §3.5 的客户端格式化（csv/json/md/xlsx/plain）与交付（return/print/file）。

**改动**：
- `asgk/_format.py`（新建）：按 data_type 分发格式化（csv/md 纯 Python，
  xlsx 用 pandas+openpyxl，json 标准库），不支持组合抛 ValueError
- `asgk/_output.py`（新建）：return/print/file 三态
- `asgk/_contract.py`：`@source` / `@capability` 加 `data_type` +
  `supported_formats` 字段（客户端注册表，与服务端注册表对齐）
- asgk 业务函数加 `format=None, output='return', path=None` 可选参数（不传时
  零破坏），在函数返回前过 `_format` + `_output`
- `asgk/tests/test_format.py`（各格式 × 各数据类型）

**验收**：
- ✅ `tencent_quote(['600519'], format='md')` 返回 kv 型 markdown（实测 贵州茅台 实时数据）
- ✅ `margin_trading('600519', format='xlsx', output='file', path=...)` 生成 5132 字节 xlsx 文件
- ✅ F10(text 型) 请求 csv 报 ValueError（在客户端报错，不打扰服务端）
- ✅ 不传 format 时行为不变（零破坏，182 测试全绿）
- 注：tencent_quote 是 kv 型不支持 csv（计划写 csv 是 table 型场景，margin_trading 已验证）

**状态**：✅ 已完成（commit 待提交）

**实施备注**（设计调整）：
- **装饰器注入而非逐函数改签名**：@source 装饰器拦截 format/output/path 参数（不传给业务函数），
  在返回值上过格式化层。这样 45 个业务函数零改动——调用方传 format= 时装饰器处理，
  不传时原样返回。比计划"业务函数加参数"侵入性小得多。
- **data_type 声明覆盖全量函数**：为 45 个 @source 函数补了 data_type（kv/table/series/text），
  驱动格式校验。未声明的兜底按返回类型推断。

**依赖**：T1（用 data_type 字段；与 T2/T3 并行）

---

## T5 — CLI 入口

**目标**：实现 `asgk-cli`（`asgk quote 600519 --format json`），兑现
asgk-contract.md 第六节承诺。

**改动**：
- `asgk/cli.py`（新建）：用 registry() 自动发现能力，注册为子命令；
  `--format/--output/--path/--source/--sources` 参数
- `asgk/__main__.py`（新建）：`python -m asgk` 入口
- pyproject.toml：注册 `asgk` console_scripts 入口
- `asgk/tests/test_cli.py`

**验收**：
- ✅ `asgk quote 600519` 打印 md 表格（实测 贵州茅台 实时数据）
- ✅ `asgk quote 600519 --format json` 打印 JSON
- ✅ `asgk quote --sources` 列出源（查服务端 /v1/sources）
- ✅ 多值 codes（`asgk quote 600519 000001`）+ 可选参数 --flag 正确传递
- ✅ 全套测试绿（client 196 / server 93 / sgw 101）

**状态**：✅ 已完成（commit 待提交）

**实施备注**：
- **registry 自动发现**：@source(cli=...) 声明的函数自动注册为子命令，无需手维护命令表。
- **参数映射**：inspect 签名驱动——list[str] 参数收集多值位置参数（nargs="*"），
  有默认值参数暴露为 --flag，必填参数 nargs="?" 让 --sources 可单独用（取数时校验）。
- **meta.wrapped**：_contract.py 的 SourceMeta 增加 wrapped 字段存 @source 包装后的函数，
  CLI 直接调它（含格式化注入），避免通过模块重导入取 wrapper。

**依赖**：T4（用格式化层）；T2/T3（要有真实能力可调）

---

## T6~T10 — 各难度梯队迁移（共享模式：T2 验证后铺开）

每个梯队沿用 T2 的模式：服务端加 capability 实现 + 客户端 em_get 路由扩展。
为控制 commit 粒度，**每个能力一个 commit**（单一职责，可独立回滚）。

### T6 — 低成本梯队：东财 push2 族（具名能力，按共享机制分组 commit）

**路线决策（B）**：走具名能力，不走 emquery 通用转发。客户端发语义参数
（pool_type/code/date），服务端持有全部上游知识（URL/ut/字段映射）。曾尝试
emquery（§3.4 em_get 枢纽）方案但回退——它是改良版透明代理，客户端仍持 URL，
未兑现 §2 "纯数据消费者"愿景。每个具名能力 = 一个 commit，按共享机制分组：

**进度**：
- ✅ **limitup_pool**（1 能力覆盖 4 函数：em_zt_pool/em_zb_pool/em_dt_pool/em_yzt_pool）：
  四池共享 _em_zt_api（同 push2ex 端点系 + ut/dpt 参数），用 pool_type 参数区分。
  字段映射（c→code, p/1000→price, zttj→zt_stat）留客户端（§6.3 纯计算）。
  实测 em_zt_pool 经具名能力返回 79 条真实涨停数据。
- ✅ **stock_info + concept_blocks**（push2.py，共享 _secid 市场前缀逻辑）：
  字段映射（f57→code, f14→name 等）下沉服务端。实测 concept_blocks 返回 28 个板块
  （食品饮料/白酒Ⅲ/白酒Ⅱ），客户端发 {code} 零上游知识。stock_info 端点在本环境
  被拒连（push2.eastmoney.com/api/qt/stock/get 风控，同 host 的 slist/get 可达），
  非代码问题，回退路径正确。

**待做**（按共享机制分组，每组一个 commit）：
- fund_flow（push2his fflow：minute + daykline 两函数共享）
- industry_comparison（push2 clist/get）
- top10_holders（emweb PageSDGD/PageSDLTGD 两函数共享）
- fund_flow（push2his fflow：minute + daykline 两函数共享 fflow 端点系）
- industry_comparison（push2 clist/get，f 字段表 + fs 参数）
- top10_holders（emweb PageSDGD/PageSDLTGD 两函数共享）
- board_constituents（push2 clist/get + 名称→代码解析）
- news（search-api JSONP + np-weblist）
- sentiment（ths_hot_list + em_hot_rank/em_hot_concept POST）
- reports（reportapi report/list 分页）
- 其余独立函数

**验收**（每个 commit）：该函数经服务端返回真实数据 + 客户端零 URL + 测试绿

**依赖**：T2（模式验证）

### T7 — 中成本梯队：编码/解析（~7 个能力）

含 GBK 解码、CSV split、JSONP 剥壳、xlsx 解析、字段索引数组。

**能力清单**：
- quote: baidu_kline_with_ma（ResultCode 风控判定 + CSV keys/rows）
- option: sina_option_codes / sina_option_tquote / sina_option_greeks
  （GBK + 字段索引 + greeks 跳空串）
- base: sina_financial_report
- announce: cninfo_announcements（POST form + orgId 映射预取）
- sentiment: cninfo_irm（两步 POST 流）
- news: cls_telegraph

**验收**：同 T6

**依赖**：T2

### T8 — 高成本梯队：算法/协议硬骨头（5 个，逐个 commit）

这是重构的核心收益所在，每个都是独立工程：

**T8.1 — mootdx TCP 客户端池**（解决 5 个直连函数）
- 服务端 `asgk_server/sources/mootdx.py`：内嵌 mootdx 客户端池
  （线程安全 + 长连接保活 + heartbeat），实现 bars/quotes/transaction/finance/f10
  五个能力
- 客户端 mootdx_bars/quotes/transaction/finance/f10 切到服务端
- **这是新架构最大收益**：5 个 TCP 函数首次走代理，有熔断保护

**T8.2 — legulegu CSRF 会话**（解决 2 个回退函数）
- 服务端 `asgk_server/sources/legulegu.py`：持有 cookie jar，CSRF 两步流在
  服务端闭环（解决无状态代理无法保持会话的问题）
- market_pe_lg / market_pb_lg 切到服务端，不再直连

**T8.3 — 百度 curl_cffi 指纹**
- 已在 sgw 的 egress 实现，搬入服务端 egress.py；baidu_kline capability 标
  egress_client=curl_cffi
- （若 T7 已含 baidu_kline，则此项并入 T7）

**T8.4 — chip cyq.js 执行**
- 服务端 `asgk_server/sources/chip.py`：py_mini_racer 执行 cyq.js（从客户端
  搬入），含 K线获取 + 百度降级链

**T8.5 — cls 签名 + 东财 POST 签名**（若 T6/T7 未覆盖签名算法）
- 服务端实现 md5(sha1) 签名 + 东财 POST body 构造

**验收**（每个）：该函数经服务端返回真实数据 + fail-closed/降级正确 + 测试绿

**依赖**：T2（mootdx/legulegu 无依赖 T6/T7，可提前）

---

## T9 — 文档下载能力（PDF/xlsx 原文，新能力类型）

**目标**：实现 §3.7 的文档下载能力。这是当前项目完全缺失的能力（announce 只
返回 url 不下载）。文档型与结构化数据性质不同（存原始 bytes、不走格式化层、
file 交付、体积上限保护），独立设计。

**改动**：
- `asgk_server/capabilities/docs.py`（新建）：文档下载能力适配器
  - `announce_pdf(anno_id)`：巨潮公告 PDF 原文下载（解析 detail 页拿 PDF 直链 → 下载 bytes）
  - `report_pdf(info_code)`：研报 PDF 原文下载
  - cache_policy="document"（§3.6c 第七类：30天 TTL + 存原始 bytes + 体积上限）
- `asgk_server/cache.py`：文档型 cache 支持
  - 存原始 bytes 文件（`<cache_dir>/_docs/<doc_id>.pdf`），不走 JSON 序列化
  - **体积上限 + LRU 淘汰**（单文件 ≤20MB，总文档 cache ≤2GB）——结构化数据无此问题，文档型必须管
- `asgk/cache_policy.py`：加 document 类型映射
- asgk 客户端：`announce_pdf` / `report_pdf` 函数（返回 bytes，output 默认 file）
- 测试：文档下载 + cache 命中 + 体积上限淘汰

**验收**：
- `announce_pdf(anno_id)` 下载真实 PDF，返回 bytes
- 同 anno_id 第二次命中 cache（不重新下载）
- 超 2GB 时 LRU 淘汰旧文档
- CLI：`asgk announce_pdf <id> --output file --path x.pdf`

**依赖**：T1.5（cache 机制，含文档型支持）；T6/T7 的 announce/report 结构化能力（提供 annoId/infoCode）

---

## T11 — 废弃 sgw + 服务端 systemd 部署

**目标**：sgw 标记废弃，asgk-server 接管部署。

**改动**：
- `packages/sgw/README.md`：标记 DEPRECATED，指向 asgk-server
- 新建 `packages/asgk-server/scripts/asgk-server-service.sh`（从 sgw-service.sh
  改造：systemd unit + uv tool install + 状态目录保持 `~/.local/share/sgw/`→
  `~/.local/share/asgk-server/`，或保持原路径兼容）
- `test_endpoint_inventory.py`：重构为「能力注册对账」（从 URL 对账改为
  capability 注册一致性检查），或随 sgw 废弃而删除
- 版本号 asgk-server 1.0.0

**验收**：
- asgk-server 经 systemd 启动，全部能力可用
- sgw 停止后所有 asgk 函数仍正常（经 asgk-server）
- 部署文档更新

**依赖**：T6~T10 全部完成（所有能力已迁移）

---

## T12 — 文档收尾

**目标**：更新所有文档反映新架构。

**改动**：
- `SKILL.md`：调用方式更新（双入口 + source/format 参数示例）
- `references/*.md`：去掉 URL/协议细节，改为能力描述 + format/output 用法
- `design.md` / `gateway-design.md` / `asgk-contract.md`：标注被
  `capability-proxy-design.md` 取代的部分
- `AGENTS.md` §2/§3：架构描述更新（能力代理取代透明代理）
- `data-source-risk-control.md`：迁移进度更新（全部经服务端，无直连）

**验收**：文档与代码一致，无过时描述

**依赖**：T11

---

## 任务总览（commit 计数估算）

| 任务 | commit 数 | 说明 |
|------|----------|------|
| T1 骨架 | 1 | 服务端 + 流量内核（cache 占位） |
| T1.5 cache refactor | 1 | 存结构化数据 + 语义key + per-source + 六类分档 |
| T2 quote 试点 | 1 | 端到端验证 |
| T3 datacenter 族 | 1 | 13 能力整体（共享适配器） |
| T4 格式化层 | 1 | _format + _output |
| T5 CLI | 1 | cli.py |
| T6 push2 族 | ~18 | 每能力 1 commit |
| T7 编码解析族 | ~7 | 每能力 1 commit |
| T8 硬骨头 | 5 | mootdx池/legulegu/百度/chip/签名 |
| T9 文档下载 | 1 | PDF/xlsx 原文（新能力，存bytes+体积上限）|
| T11 废弃 sgw | 1 | 部署切换 |
| T12 文档 | 1 | 收尾 |
| **合计** | **~38** | |

> T6/T7 的"每能力 1 commit"可视实施时合并同模块的（如 limitup 4 个池函数
> 共用一个适配器，可 1 commit）。实际 commit 数预计 25~36。

## 执行建议

1. **先 T1→T2**：验证整个架构可行（最大风险点），再铺开
2. **T3/T4/T5 并行**：互不依赖，可同时推进
3. **T6→T7→T8 按难度递增**：先易后难，每完成一档积累信心
4. **T8.1 (mootdx) 优先级最高**：它是新架构最大收益（5 个 TCP 函数首次有代理保护），
   可在 T6 之前插队
5. **每任务完成后盘中实测**：确保真实数据通路正常，不留隐患到最后
