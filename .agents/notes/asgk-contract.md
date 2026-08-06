# asgk 接口契约（P1 移植规范）

> **架构已演进**：本文件约束的「两层函数模型」（业务函数 + `em_get` 底层）是 P1
> 阶段的契约，基于 sgw 透明网关。能力代理重构后（[capability-proxy-design.md](capability-proxy-design.md)），
> 业务函数内部从「拼 URL + em_get」改为「`_server_call(capability, params)` 调服务端」，
> 服务端持有全部上游知识。
>
> **⚠️ 进一步演进（CLI 并入 server 包后）**：本文件描述的**整个客户端库**
> （`@source` 装饰器 / `em_get` / 业务函数 / `skills/a-stock-data/scripts/asgk/`）
> 已于 CLI 重构时**整体删除**。当前 CLI 是纯 HTTP 客户端
> （`packages/asgk-server/asgk_server/cli/`），命令映射见 `cli/commands.py`，
> 服务端能力见 `asgk_server/capabilities/`。
>
> **仍有参考价值的部分**：业务函数的**返回结构约定**（dict/list/str/bytes 的字段
> 定义）——这些领域知识已沉淀到 `skills/a-stock-data/references/*.md` 和服务端
> 能力实现里，本文记录了它们的最初设计来源。
>
> **已完全废弃的部分**：`@source` 装饰器、`em_get` 底层、两层函数模型、tier 驱动、
> `ASGK_GW`/sgw 回退路径——全部随客户端库删除。

本文件是 P1（scripts 共享库移植）的接口契约，约束 14 个模块、43 个端点的移植。所有移植的函数必须遵循此契约。

> 相关：`gateway-design.md`（网关/缓存/分档机制）、`scripts-library-port.md`（P1 待办）。

## 一、两层函数模型

| 层 | 函数 | 输入 | 返回 | 经网关? |
|----|------|------|------|---------|
| 底层 | `em_get(url, params, headers, timeout, tier)` | 原始 URL | `requests.Response` | 由 tier + ASGK_GW 决定（P0 已实现） |
| 业务 | `tencent_quote(codes)` / `eastmoney_reports(code)` 等 | 语义参数（code/date） | **结构化 dict / list** | 由 `@source(via=)` 声明 |

**核心约束**：业务函数返回结构化 Python 对象（dict/list），**不返回 Response**。内部调 `em_get` 拿 Response 再解析。这与 ref 现有签名一致（抽样验证：`tencent_quote -> dict`、`eastmoney_reports -> list[dict]`、`dragon_tiger_board -> list[dict]`）。

调用方（agent/CLI）只接触业务函数，不直接调 em_get（除非需要底层控制）。

## 二、@source 装饰器（元数据声明）

每个业务函数用 `@source` 声明元数据，一处定义，驱动缓存档位/指纹 strip/文档/CLI：

```python
from asgk._contract import source
from asgk.em_proxy import em_get

@source(tier="S", via="gateway", cli="block")
def eastmoney_concept_blocks(code: str) -> list[dict]:
    """个股所属板块/概念归属"""
    r = em_get(SLIST_URL, params={"spt": 3, "security_code": code, ...}, tier="S")
    return r.json()["result"]["data"]
```

字段：
- `tier`：缓存档位 P/L/S/R/N（先验方案，gateway-design §3.4.6）。
- `strip`：响应哈希需剔除的动态字段列表（§3.4.7），默认 None。已知需 strip 的：东财全球资讯 `req_trace`、五档盘口 `servertime`。
- `via`：`"gateway"`（风控源，经 em_get + 网关）/ `"direct"`（腾讯/百度/新浪/mootdx，直连不经网关）。
- `cli`：对应 CLI 子命令名，None = 不暴露为命令行。

**运行时行为**：装饰器纯声明，零开销——不改变函数行为（tier 仍由函数体内 em_get 调用传入）。元数据存到 `fn._asgk_meta`，供 CLI 注册、文档生成、离线分析遍历（`asgk._contract.registry()`）。

## 三、统一签名规范

所有业务函数遵循：

- **code 参数**：统一 6 位代码字符串（`"600519"`）。函数内部做市场前缀归一化（6/9 开头→sh，8→bj，其余→sz）。调用方不传 `sh600519`。
- **date 参数**：可选，默认最近交易日；格式 `"YYYY-MM-DD"`。
- **返回类型**：`list[dict]`（列表型：研报/龙虎榜/资金流序列）或 `dict`（单对象/映射：行情）。
- **空结果**：返回 `[]` 或 `{}`，**不返回 None**。业务空（如盘前查龙虎榜）返回空结构。
- **异常**：网络异常向上抛（调用方决定重试/降级）；业务空不抛异常。

## 四、模块组织

```
asgk/
├── __init__.py        暴露所有业务函数
├── _contract.py       @source 装饰器 + 注册表（已实现）
├── em_proxy.py        em_get（底层，P0 已实现）
├── client.py          mootdx TCP 封装（直连）
├── quote.py           行情：腾讯/百度（直连）
├── reports.py         研报：东财/同花顺/iwencai
├── signal.py          信号：热点/北向/龙虎榜/解禁/行业
├── capital.py         资金面：融资融券/大宗/股东户数/分红/资金流
├── news.py            新闻：东财个股/财联社/全球资讯
├── base.py            基础数据：mootdx财务/F10/东财信息/新浪三表
├── announce.py        公告：巨潮
├── limitup.py         打板：涨停/炸板/跌停池 + 情绪
├── option.py          ETF期权：合约/T型报价/希腊字母
├── sentiment.py       舆情：互动易/热榜/人气榜
├── valuation.py       估值公式（本地计算，无网络）
└── cli.py             CLI 入口（P1 后期）
```

## 五、完整端点映射表（43 端点）

P1 移植时逐行对照。档位依据 gateway-design §3.4.1-3.4.5 的时效性分析。

### quote.py（行情，直连不经网关）

| 函数 | tier | strip | via | cli | 说明 |
|------|------|-------|-----|-----|------|
| tencent_quote | R | - | direct | quote | PE/PB/市值/换手（腾讯，GBK） |
| baidu_kline_with_ma | R | - | direct | kline | 日K带MA5/10/20 |

### reports.py（研报，经网关）

| 函数 | tier | strip | via | cli | 说明 |
|------|------|-------|-----|-----|------|
| eastmoney_reports | P | - | gateway | report | 个股研报列表+评级 |
| eastmoney_industry_reports | P | - | gateway | - | 行业研报 |
| ths_eps_forecast | S | - | gateway | - | 一致预期EPS（同花顺） |
| iwencai_search | P | - | gateway | - | NL语义搜研报（需key） |

### signal.py（信号，经网关）

| 函数 | tier | strip | via | cli | 说明 |
|------|------|-------|-----|-----|------|
| ths_hot_reason | S | - | gateway | - | 当日强势股+题材归因 |
| hsgt_realtime | R | - | gateway | - | 北向分钟流向（盘中R/盘后S） |
| eastmoney_concept_blocks | S | - | gateway | block | 个股板块归属 |
| eastmoney_fund_flow_minute | R | - | gateway | - | 分钟资金流 |
| dragon_tiger_board | S | - | gateway | - | 个股龙虎榜（盘后定稿） |
| lockup_expiry | S | - | gateway | - | 解禁日历 |
| industry_comparison | R | - | gateway | - | 行业排名（盘中R/盘后S） |
| daily_dragon_tiger | S | - | gateway | - | 全市场龙虎榜 |

### capital.py（资金面，经网关）

| 函数 | tier | strip | via | cli | 说明 |
|------|------|-------|-----|-----|------|
| margin_trading | S | - | gateway | - | 融资融券（日级） |
| block_trade | S | - | gateway | - | 大宗交易（日级） |
| holder_num_change | L | - | gateway | - | 股东户数（季度） |
| dividend_history | P | - | gateway | - | 分红历史（定稿） |
| stock_fund_flow_120d | S | - | gateway | fundflow | 个股资金流120日 |

### news.py（新闻）

| 函数 | tier | strip | via | cli | 说明 |
|------|------|-------|-----|-----|------|
| eastmoney_stock_news | N | - | gateway | - | 个股新闻 |
| cls_telegraph | N | - | direct | - | 财联社电报（本地签名） |
| eastmoney_global_news | N | req_trace | gateway | - | 全球资讯（strip req_trace） |

### base.py（基础数据）

| 函数 | tier | strip | via | cli | 说明 |
|------|------|-------|-----|-----|------|
| mootdx_finance | L | - | direct | - | 季报快照（mootdx TCP） |
| mootdx_f10 | P | - | direct | - | 公司资料（mootdx TCP） |
| eastmoney_stock_info | S | - | gateway | - | 行业/股本/市值 |
| sina_financial_report | L | - | direct | - | 财报三表（新浪） |

### announce.py（公告，经网关）

| 函数 | tier | strip | via | cli | 说明 |
|------|------|-------|-----|-----|------|
| cninfo_announcements | P | - | gateway | announce | 巨潮公告检索 |

### limitup.py（打板，经网关）

| 函数 | tier | strip | via | cli | 说明 |
|------|------|-------|-----|-----|------|
| em_zt_pool / em_zb_pool / em_dt_pool / em_yzt_pool | R | - | gateway | - | 涨停/炸板/跌停/昨涨停池（盘中R/盘后S） |
| ths_limit_up_pool | R | - | gateway | - | 涨停揭秘 |
| limit_up_sentiment | R | - | gateway | - | 打板情绪 |

### option.py（ETF期权，直连）

| 函数 | tier | strip | via | cli | 说明 |
|------|------|-------|-----|-----|------|
| sina_option_codes | S | - | direct | - | 合约清单 |
| sina_option_tquote | R | - | direct | - | T型报价 |
| sina_option_greeks | R | - | direct | - | 希腊字母+IV |

### sentiment.py（舆情）

| 函数 | tier | strip | via | cli | 说明 |
|------|------|-------|-----|-----|------|
| cninfo_irm | P | - | gateway | - | 互动易问答 |
| ths_hot_list | R | - | gateway | - | 同花顺热榜 |
| em_hot_rank | R | - | gateway | - | 东财人气榜 |
| em_hot_concept | S | - | gateway | - | 概念命中 |

### valuation.py（估值，本地计算无网络）

| 函数 | tier | strip | via | cli | 说明 |
|------|------|-------|-----|-----|------|
| forward_pe | - | - | - | - | 前向PE（纯计算） |
| pe_digestion | - | - | - | - | PE消化时间 |
| calc_peg | - | - | - | - | PEG |
| full_valuation | - | - | - | valuation | 单票估值全景（串联多源） |

> valuation.py 不走网络，无 tier/via；但它会被 CLI 暴露（`asgk valuation`），内部调用其他业务函数。

## 六、CLI 契约

```
asgk <command> <code> [选项] [--format json|table]
```

- 每个 `@source(cli=...)` 的函数自动注册为子命令。
- `<code>`：6 位股票代码。
- `--format`：默认 table（人读），json 给管道。
- 示例：`asgk quote 600519`、`asgk report 600519`、`asgk block 600519`、`asgk fundflow 600519`、`asgk valuation 600519`。

CLI 实现（`asgk/cli.py`）在 P1 后期，用 `asgk._contract.registry()` 自动发现所有 `cli != None` 的函数注册为子命令。

## 七、移植流程（每个端点）

1. 从 ref SKILL.md 提取函数实现。
2. 改 import：`em_get` 从 `asgk.em_proxy` 引入（接口不变）。
3. 加 `@source(tier=, via=, cli=)` 声明（按本文件第五节表）。
4. 确认返回结构化 dict/list（不返 Response）。
5. code 参数归一化（函数内做前缀处理）。
6. smoke test：用茅台 600519 验证返回非空。
