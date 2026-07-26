# akshare 集成方案分析：source gateway vs 封装 package

> **状态**：Draft，方法论分析（非执行计划）
> **分支**：`feat/akshare-merge`
> **日期**：2026-07-26
> **关联**：[akshare-merge-design.md](akshare-merge-design.md)（合成方案的执行 plan）

---

## 0. 问题

用户的核心质询：把 akshare 能力引入本项目，是

- **(A) 做成完全从 source 的 gateway**（类似 sgw，把 akshare 当数据源代理）？
- **(B) 封装一个 akshare package**（pip install akshare 后封装一层）？
- 还是有第三条路？

本文基于 sgw 实现、akshare 源码、依赖体积的事实分析，给出结论。

---

## 1. 关键事实（决策依据）

### 1.1 sgw 已经是 source gateway，且已是"东财/同花顺 source 代理"

核对 `packages/sgw/sgw/proxy.py`：

```python
PROXIED_DOMAIN_SUFFIXES = (".eastmoney.com", ".10jqka.com.cn")
# handle() 按 host 后缀路由到限流组，缓存，转发 GET
def handle(self, target_url, params, tier_header):
    host = urlparse(target_url).netloc
    group = self.group_of(host)  # 按域名后缀判定
    ...
    r = requests.get(target_url, params=params, ...)  # 透明转发
```

机制：客户端发 `GET http://gw?u=<原始URL>&<params>`，sgw 按域名后缀路由到限流组（东财组/同花顺组），缓存，透明转发。**这是按"上游 source 域名"做的 source gateway**。

### 1.2 akshare 的请求层与 sgw 完全同构

akshare A 股核心模块（stock/stock_feature/stock_fundamental）的 HTTP 调用分布：

| 方法 | 调用数 | 占比 |
|------|-------|------|
| GET | 541 | 93% |
| POST | 41 | 7% |

POST 主要是巨潮热度榜/披露、东财热度榜（emappdata），与 P0/P1 候选接口（十大股东/业绩/筹码/板块/回购/质押）**无关**——后者几乎全是东财 datacenter GET。

**典型 akshare 东财接口实现**（`stock_repurchase_em.py`）：

```python
url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
params = {"reportName": "RPTA_WEB_GETHGLIST_NEW", "columns": "ALL", ...}
r = requests.get(url, params=params)
data_json = r.json()
temp_df = pd.DataFrame(data_json["result"]["data"])  # ← 解析
```

**对比 asgk 现有 `_datacenter()`**（`asgk/_datacenter.py`）：

```python
def datacenter(report_name, filter_str="", page_size=50, ...):
    # 同样的 datacenter-web.eastmoney.com/api/data/v1/get
    # 同样的 reportName/columns 参数
    # 返回 list[dict]（契约层统一类型；实现层可用 pandas 解析后转 dict）
```

**结论**：akshare 的东财接口与 asgk 的 `_datacenter()` 调用的是**同一个端点**，参数结构相同，唯一区别是 akshare 用 pandas 解析、asgk 用纯 dict 解析。

### 1.3 依赖体积对比

| 维度 | akshare 全量 | asgk 现状 |
|------|-------------|----------|
| pandas + numpy | ✅ 必需 | ⚠️ 已装（mootdx 传递依赖，asgk 未直接用） |
| lxml + beautifulsoup4 | ✅ 必需 | ⚠️ lxml 已声明（asgk pyproject 有但未真用）；bs4 未装 |
| openpyxl + xlrd | ✅ 必需（读 Excel） | ❌ 不用 |
| curl_cffi | ✅ 必需（JA3 绕过） | ❌ 不用 |
| mini-racer / akracer | ✅ 必需（JS 执行） | ⚠️ py-mini-racer 已装（mootdx 传递依赖） |
| jsonpath | ✅ 必需 | ❌ 不用 |
| 估算安装体积 | **~80-120MB** | ~160MB+（已含 mootdx 拉入的 pandas/numpy/mini-racer） |

> **修正（2026-07-27）**：早先版本误称 asgk "零重依赖 ~5MB"。核查 `uv.lock` 后纠正：pandas(50M)/numpy(33M)/py-mini-racer(48M)/lxml(12M) 早已是 mootdx 传递依赖，asgk 实际闭包 ~160MB+。详见 [akshare-port-feasibility.md §5.1](akshare-port-feasibility.md)。
>
> 因此 asgk 与 akshare 在 pandas/lxml/mini-racer 上**依赖重叠**，方案对比的真正差异不在"是否引入重依赖"，而在：① curl_cffi（akshare 需 JA3 绕过，asgk 走网关无需）② akshare 全量包绕过 sgw（违反 §2）③ akshare 无统一请求门面无法 hook。

### 1.4 akshare 没有统一的"请求门面"

akshare 有 `utils/func.py:fetch_paginated_data`（分页辅助）和 `request.py:make_request_with_retry_json`（带重试的 GET），但**绝大多数接口直接在函数体内 `requests.get`**，没有强制走统一层。这意味着无法用"替换请求层"一刀切地让 akshare 全量走 sgw。

---

## 2. 三种方案评估

### 方案 A：把 akshare 做成 source gateway（新增一个 akgw）

**设想**：像 sgw 代理东财域名那样，新建一个 `akgw` 网关，把 akshare 当数据源代理。

**问题**：这其实**不需要新建**——sgw 已经是 source gateway，且 akshare 的东财源已经命中 sgw 的 `.eastmoney.com` 后缀。

**真问题在于"代理粒度"**：

- sgw 代理的是**HTTP 端点**（`?u=<URL>`），akshare 的价值是**业务函数**（`stock_repurchase_em()` 含分页+字段重命名+解析）。
- 网关层只能代理 HTTP，**业务逻辑（分页、字段映射、pandas→dict 转换）必须在客户端**。
- 所以"akshare source gateway"要么是：
  - **A.1**：sgw 已做的事（按域名代理）—— akshare 东财源已天然命中，无需新建
  - **A.2**：把 akshare 的业务函数搬到网关侧（网关变胖，承担解析）—— 违反 sgw 的"薄代理"定位

**结论**：方案 A 在语义上已被 sgw 覆盖（A.1），A.2 会让网关承担不该承担的业务逻辑。

### 方案 B：封装 akshare package（pip install akshare + 包装层）

**设想**：`pip install akshare`，在 asgk 内写一层包装，调用 `akshare.stock_repurchase_em()` 后转成 `list[dict]`。

**优点**：
- 实现快（复用 akshare 的解析逻辑）
- 上游更新自动同步（重装 akshare）

**致命问题**：

| 问题 | 影响 |
|------|------|
| akshare 绕过 sgw 直连东财 | **违反 AGENTS.md §2**（风控源必经网关），1000 agent 并发直接封 IP — 这是首要否决理由 |
| akshare 的请求层无 hook | 无法强制让 akshare 走 sgw（akshare 函数体内直接 `requests.get`） |
| 引入 curl_cffi (31M) | TLS 指纹绕过是反反爬对抗；asgk 已有 em_get 走网关，不需要每客户端带 JA3 |
| 上游 break 风险 | akshare 接口签名/返回结构频繁变动，封装层脆弱 |

> 注：pandas/lxml/mini-racer 的体积**不是**否决理由（asgk 已通过 mootdx 间接装了）。真正致命的是绕过 sgw + 无请求 hook。

**结论**：方案 B 违反项目核心约束 §2（风控源必经网关），**不可行**。

### 方案 C：akshare 作 ref 蓝本，移植解析逻辑到 asgk（前一轮的合成方案）

**设想**：akshare 仅作只读参考（像 `ref/a-stock-data`），asgk 新增模块时**复制其端点 + 参数 + 字段映射逻辑**，用 asgk 自己的 `em_get`/`_datacenter`（走 sgw）。实现层可自由用已装依赖（pandas/lxml/mini-racer），契约层返回 `list[dict]` + `to_df()` 包装。

**这正是 asgk 现有模块的做法**。核对 `asgk/capital.py`：

```python
@source(tier="S", via="gateway")
def margin_trading(code, page_size=30):
    data = _datacenter("RPTA_WEB_RZRQ_GGMX", filter_str=f'(SCAME="{code}")', ...)
    # ↑ 与 akshare 调同一东财端点，但经 sgw 网关，返回 list[dict]
```

**优点**：

| 维度 | 表现 |
|------|------|
| 流量管控 | ✅ 所有东财请求经 sgw（复用 `em_get`/`_datacenter`） |
| 依赖体积 | ✅ 零新增（实现层用已装的 pandas/lxml/mini-racer） |
| 并发安全 | ✅ 共享 sgw 限流配额 |
| 上游隔离 | ✅ akshare break 不影响 asgk（只参考其端点/字段） |
| 实现成本 | 中（每接口 ~30-60 行，参考 capital.py 范式） |
| 维护成本 | 低（asgk 接口稳定，不追 akshare 上游） |

**缺点**：
- 移植需逐接口手写（不能自动同步上游）
- akshare 的非东财源（乐咕/雪球/百度股市通）需新增直连客户端

**结论**：方案 C 是**唯一同时满足 §2 流量管控 + 并发安全 + 不绕过网关**的方案，且与 asgk 现有架构完全一致。

---

## 3. 三方案对比账

| 维度 | A. source gateway | B. 封装 package | **C. ref 蓝本移植** |
|------|------------------|----------------|-------------------|
| 是否新建基础设施 | A.1 无需（sgw 已覆盖）/ A.2 需建胖网关 | 否 | **否** |
| §2 流量管控合规 | A.1 ✅ / A.2 ✅ | ❌ 绕过 sgw | **✅** |
| 依赖体积 | ✅ | ❌ 引入 curl_cffi | **✅ 零新增（复用已装依赖）** |
| 并发安全（100~1000 agent） | ✅ | ❌ 直连封 IP | **✅** |
| 业务逻辑归属 | 网关（A.2）或客户端 | akshare 内 | **asgk 客户端** |
| 上游同步 | 不适用 | 自动（但脆弱） | **手动按需** |
| 与 asgk 现有架构一致 | 部分 | 否 | **完全一致** |
| 实现成本 | A.1 低 / A.2 高 | 低 | 中 |
| 长期维护成本 | A.1 低 / A.2 高 | 高（上游 break） | **低** |

**结论**：**方案 C 胜出**。方案 A 在语义上已被 sgw 覆盖（无需新建），方案 B 违反核心约束。

---

## 4. 关键洞察：sgw 已经是"akshare 的 source gateway"

回答用户原问题"是否有必要做成完全从 source 的 gateway"——

**已经有，就是 sgw。** sgw 按域名后缀代理（`.eastmoney.com`/`.10jqka.com.cn`），而 akshare 的 A 股接口 93% 是东财/同花顺 GET，**天然命中 sgw 的现有限流组**。核对 akshare 10 个 P0 接口的数据源：

| akshare 接口 | 数据源域名 | sgw 覆盖？ |
|-------------|----------|-----------|
| 十大股东 `stock_gdfx_top_10_em` | datacenter-web.eastmoney.com | ✅ 已覆盖 |
| 业绩预告 `stock_yjyg_em` | datacenter.eastmoney.com | ✅ 已覆盖 |
| 筹码分布 `stock_cyq_em` | push2his.eastmoney.com | ✅ 已覆盖 |
| 板块成份股 `stock_board_concept_cons_em` | push2.eastmoney.com | ✅ 已覆盖 |
| 回购 `stock_repurchase_em` | datacenter-web.eastmoney.com | ✅ 已覆盖 |
| 高管增减持 `stock_hold_management_detail_em` | datacenter-web.eastmoney.com | ✅ 已覆盖 |
| 机构调研 `stock_jgdy_detail_em` | datacenter-web.eastmoney.com | ✅ 已覆盖 |
| 财报三表 `stock_three_report_em` | datacenter-web.eastmoney.com | ✅ 已覆盖 |
| 融资融券（官方）`stock_margin_sse` | query.sse.com.cn | ❌ 需新增（交易所源） |
| 个股 PE/PB 分位 `stock_a_indicator_lg` | legulegu.com / eniu.com | ❌ 需新增（乐咕源） |

**10/10 中 8 个命中 sgw 现有组**。剩下 2 个是非风控源（交易所/乐咕），按 §2 可直连或按需加组。

**所以"做成 source gateway"这件事，sgw 已经做完了**。剩下的工作不是建新网关，而是把 akshare 的业务函数（端点+参数+字段映射）移植到 asgk，让它们调用现成的 `em_get`/`_datacenter`（自动走 sgw）。

---

## 5. 网关侧需要的小调整（可选，非阻塞）

方案 C 不需要重构 sgw，但有两个可选增强：

### 5.1 乐咕源（legulegu/eniu）处置

乐咕是非风控源（无封 IP 历史），按 §2 可直连。两个选项：

| 选项 | 做法 | 适用 |
|------|------|------|
| 直连 + asgk 内自律限流 | 复用 `em_proxy._direct_throttle` 模式，进程内 1 req/s | 推荐（乐咕无封 IP 风险） |
| 进 sgw 新建 `legu` 组 | sgw config 加 `{"name":"legu","rps":1,"domains":["legulegu.com","eniu.com"]}` | 若担心未来乐咕加风控 |

**倾向**：直连 + 自律限流（避免网关成为单点依赖）。

### 5.2 交易所源（sse/szse）处置

融资融券官方源（`query.sse.com.cn` / `report.szse.cn`）是非风控源，但请求频率高（日级全市场）。建议走 sgw 新建 `exchange` 组，与东财组隔离（避免交易所源被东财封禁牵连）。

### 5.3 POST 接口（akshare 7%）

sgw 当前是 GET-only。若未来要移植 akshare 的 POST 接口（巨潮热度榜/东财 emappdata），需扩展 sgw 支持 POST。**但 P0/P1 候选接口都是 GET，非阻塞**，留到 P2 按需。

---

## 6. 最终结论

### 是否做成 source gateway？

**不需要新建。sgw 已经是 akshare 的 source gateway**（按域名代理，akshare 东财源 8/10 命中现有限流组）。

### 是否封装 akshare package？

**不可行**。首要否决理由是违反 §2 流量管控（绕过 sgw 直连东财，1000 agent 并发封 IP）；其次是 akshare 无统一请求门面无法 hook 走网关；再次是引入 curl_cffi（反反爬对抗，asgk 走网关无需）。

### 推荐方案

**方案 C：akshare 作 ref 蓝本，移植解析逻辑到 asgk**。这是唯一同时满足：
- §2 流量管控（经 sgw）
- 复用已装依赖（pandas/lxml/mini-racer 已是 mootdx 传递依赖）
- 并发安全（共享限流配额）
- 与 asgk 现有架构完全一致

的方案。具体执行计划见 [akshare-merge-design.md](akshare-merge-design.md)。

### 一句话总结

> **sgw 已经是 source gateway，akshare 的东财源天然命中。剩下的不是"建网关"或"封装包"，而是把 akshare 的业务函数（端点+参数+字段映射）按 asgk 现有范式（`em_get`/`_datacenter` + `list[dict]`）移植一遍。**

---

## 7. 待评审决策点

1. **乐咕源处置**：直连+自律限流 vs 进 sgw 新建组？（倾向直连）
2. **交易所源处置**：进 sgw 新建 `exchange` 组 vs 直连？（倾向进 sgw，与东财隔离）
3. **POST 支持**：sgw 是否现在就扩展 POST？（倾向否，P2 按需）
4. **本分析文档归宿**：留 notes/ 作为方法论 vs 合并进 akshare-merge-design.md？
