# akshare 集成方案分析：架构选择（source gateway vs 封装 package vs ref 移植）

> **状态**：方法论分析（非执行计划）
> **分支**：`feat/akshare-merge`
> **最后修订**：2026-07-31
> **关联**：
> - [akshare-merge-design.md](akshare-merge-design.md)：**唯一执行计划与权威 interface inventory**（本文只引用其 ID，不复制接口表）
> - [akshare-port-feasibility.md](akshare-port-feasibility.md)：技术模式与可移植性证据
> **职责**：本文只回答"为什么选 ref-port，而不是新业务网关或封装 akshare package"。接口范围、阶段、依赖声明见 merge-design。

---

## 0. 问题

把 akshare 能力引入本项目，是：
- **(A) 做成完全从 source 的 gateway**（类似 sgw，把 akshare 当数据源代理）？
- **(B) 封装一个 akshare package**（pip install akshare 后包装一层）？
- **(C) akshare 作 ref 蓝本，逐接口移植到 asgk**？

本文基于 sgw 当前实现、akshare 固定 snapshot、依赖事实，给出结论。

**参考基线**：akshare 1.18.64 / commit `fcdbf25`（见 [merge-design §0](akshare-merge-design.md)）。所有 akshare 源码事实仅针对此 snapshot。

---

## 1. 关键事实（决策依据）

### 1.1 sgw 是 source gateway，但"后缀准入"不等于"host 已可路由"

核对 `packages/sgw/sgw/proxy.py`，sgw 有**两层**校验：

```python
# proxy.py:35-36  第一层：后缀准入
PROXIED_DOMAIN_SUFFIXES = (".eastmoney.com", ".10jqka.com.cn")

# proxy.py:243-248  第二层：exact-host 归组
def group_of(self, host):
    for suffix in PROXIED_DOMAIN_SUFFIXES:
        if host.endswith(suffix):
            return self.domain_group.get(host)   # ← host 必须精确命中 config
    return None
```

因此一个东财子域要能路由，**必须同时满足**：① 后缀属于 `.eastmoney.com`/`.10jqka.com.cn`；② 该精确 host 出现在 `config.toml` 的某个 domain group。

**当前 inventory 涉及但未在 config 精确归组的 host**（见 [merge-design §4.9](akshare-merge-design.md)）：
- `emweb.securities.eastmoney.com`（AKP-HOLD-001/002 十大股东，**非 datacenter 端点**）
- `datacenter.eastmoney.com`（AKP-EARN-001/002 业绩，path 是 `/securities/api/data/v1/get`，与 datacenter-web 不同）
- `29.push2.eastmoney.com` / `79.push2.eastmoney.com`（AKP-BOARD 板块，**编号子域**）
- `www.szse.cn`（AKP-FAILOVER-001，交易所源，当前也不在 suffix 列表）

> **结论**：不能写"`.eastmoney.com` 后缀天然覆盖 akshare 东财接口"。后缀只是**准入闸门**，精确归组是**路由闸门**，二者必须同时通过。这是 [merge-design 阶段2](akshare-merge-design.md) 的前置修正项。

### 1.2 sgw 当前不透传业务 headers

`em_get(..., headers=...)` 会把调用方 headers 发给 sgw（`skills/a-stock-data/scripts/asgk/asgk/em_proxy.py:89-100`），但 sgw 转发上游时**丢弃这些 headers**，只构造固定 User-Agent（`proxy.py:349-356`）。

影响以下接口（按 inventory）：
- AKP-FAILOVER-001（深交所 `Referer`）——Referer 当前到不了上游，会被拒。
- 同花顺 `hexin-v` cookie、乐咕 `X-CSRF-Token`/cookie——若经网关同样失效。

> 这是阶段2 的第二个前置修正：设计显式且受限的 upstream header 白名单（`User-Agent`/`Referer`/`Cookie`/`X-CSRF-Token`/`Accept`），并评估哪些 header 影响响应、须纳入 cache key。禁止透传 `Host` 等 hop-by-hop header。

### 1.3 sgw cache key 忽略 params（正确性隐患）

当前 `cache_key = f"{tier}|{target_url}"`，不含请求 params。这意味着同一 URL 不同股票、不同日期、不同页码会命中同一缓存条目——这是比 akshare 移植更优先的正确性问题，阶段2 必须改为 canonical prepared URL。

### 1.4 `_datacenter()` 只取第一页

`skills/a-stock-data/scripts/asgk/asgk/_datacenter.py:12-35` 固定 `pageNumber=1`，不读 `result.pages`。而 inventory 中大量接口是**全市场多页扫描**（AKP-HOLD-003/004、AKP-EARN、AKP-EVT、AKP-RISK 等），akshare 蓝本均遍历 `range(1, total_page+1)`。

> 不能称"akshare datacenter 接口与现有 `_datacenter()` 完全同构"。请求层结构相似，但**分页契约不同**——阶段2 须扩展 `_datacenter` 支持 `all_pages`/`max_pages`。

### 1.5 akshare 请求层与 asgk 部分同构（datacenter JSON 子集）

在固定 snapshot 中，akshare 的东财 datacenter 接口（如 `stock_repurchase_em.py`）调用：

```python
url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
params = {"reportName": "RPTA_WEB_GETHGLIST_NEW", "columns": "ALL", ...}  # +分页
r = requests.get(url, params=params)
data_json["result"]["data"]
```

asgk 现有 `_datacenter()` 调用**同一端点**、参数结构相似。**但**：
- akshare 端遍历全部分页（见 §1.4），asgk 只取第一页；
- 业绩接口用的是 `datacenter.eastmoney.com/securities/api/data/v1/get`（不同 host+path，见 §1.1），不能直接复用；
- 十大股东用的是 `emweb.securities.eastmoney.com`（非 datacenter），需 `em_get` 直调。

> 所以"请求层同构"只在 datacenter-web JSON 子集成立，且即便同构也需补分页。逐项细节见 [merge-design §4 inventory](akshare-merge-design.md)。

### 1.6 依赖事实（修正旧版错误）

| 依赖 | 当前事实 | 修订原则 |
|---|---|---|
| `lxml` | asgk **直接声明**（`pyproject.toml`，代码此前未真用） | 可直接用于 HTML helper |
| `mootdx` / `requests` | asgk 直接声明，已用 | 不变 |
| `py-mini-racer` | **mootdx 的传递依赖**（`uv.lock`） | asgk 直接 import 时应提升为**直接依赖** |
| `pandas` | **mootdx→tdxpy 的传递依赖** | 同上 |
| `numpy` | pandas 传递依赖 | 通常无需单独声明 |
| `openpyxl` | **当前 lockfile 中不存在**（pandas 把它列为 `excel` optional extra，非默认依赖） | xlsx 方案若采用则**新增直接依赖** |
| `curl_cffi` | 未声明 | **不引入**（见 §2 方案 B） |
| `akshare` | 未声明 | **保持不安装** |

> 旧版文档曾误称 asgk"零重依赖 ~5MB"，后又误称"openpyxl 已装（pandas 传递依赖）"。两者均错：pandas/mini-racer 是传递依赖（虽已装但不应被业务包直接 import 而不声明）；openpyxl 根本未装。**"零新增依赖"不成立**——任何被 asgk 直接 import 的第三方包都应显式声明。删除所有无可复现测量依据的固定安装体积数字。

### 1.7 akshare 没有统一"请求门面"

akshare 有 `utils/func.py:fetch_paginated_data` 和 `request.py` 辅助，但多数接口在函数体内直接 `requests.get`。无法用"替换请求层"一刀切让 akshare 全量走 sgw——这是方案 B 致命问题的根源。

---

## 2. 三方案评估

### 方案 A：把 akshare 做成 source gateway（新建 akgw）

**设想**：像 sgw 代理东财域名那样，新建 `akgw` 代理 akshare 数据源。

**问题**：sgw 已经是 source gateway。akshare 的东财源后缀命中 sgw 准入闸门（虽需补精确归组，见 §1.1）。真正的工作不是建新网关，而是：
- **A.1**（sgw 已做）：按域名代理 HTTP 端点；
- **A.2**（不该做）：把 akshare 业务函数（分页/字段映射/解析）搬到网关侧——违反 sgw"薄代理"定位。

业务逻辑（分页、字段映射、pandas→dict）必须在客户端（asgk）。

**结论**：方案 A 在语义上已被 sgw 覆盖（A.1），A.2 让网关承担不该承担的业务逻辑。**不新建网关**。

### 方案 B：封装 akshare package（pip install akshare + 包装层）

**优点**：实现快（复用 akshare 解析）、上游自动同步。

**致命问题**：

| 问题 | 影响 |
|------|------|
| **akshare 绕过 sgw 直连东财** | 违反 AGENTS.md §2（风控源必经网关），1000 agent 并发直接封 IP —— **首要否决理由** |
| akshare 无统一请求门面 | 无法强制让 akshare 走 sgw（函数体内直接 `requests.get`） |
| 引入 curl_cffi | TLS 指纹伪装是反反爬对抗；asgk 走网关无需每客户端带 JA3 |
| 上游 break 风险 | akshare 接口签名/返回频繁变动，封装层脆弱 |

> 注：pandas/lxml/mini-racer 体积**不是**否决理由（asgk 已通过 mootdx 间接有部分）。真正致命的是绕过 sgw + 无请求 hook。

**结论**：方案 B 违反 §2 核心约束，**不可行**。

### 方案 C：akshare 作 ref 蓝本，逐接口移植到 asgk

**设想**：akshare 作只读参考（像 `ref/a-stock-data`），asgk 新增模块时复制其端点+参数+字段映射逻辑，用 asgk 自己的 `em_get`/`_datacenter`（走 sgw）。这正是 asgk 现有模块（如 `capital.py`）的做法。

**优点**：
- ✅ §2 流量管控：东财/同花顺请求经 sgw（复用 `em_get`/`_datacenter`）
- ✅ 上游隔离：akshare break 不影响 asgk（只参考端点/字段）
- ✅ 并发安全：共享 sgw 限流配额

**不是零代价**（旧文档误称"零基础设施"）：
- ⚠️ 须先修正 sgw host 归组、header 透传、cache key、datacenter 分页（§1.1–1.4，阶段2）
- ⚠️ 须按 approved inventory 显式声明直接依赖（pandas/mini-racer/openpyxl 等，§1.6）
- ⚠️ 非东财源（乐咕/交易所）需决策直连还是进网关（[merge-design §7 决策10](akshare-merge-design.md) 风控验证）

**结论**：方案 C 是**唯一同时满足 §2 流量管控 + 并发安全 + 不绕过网关**的方案，**但需按需补齐现有基础设施的正确性缺口**，不是零基础设施成本。具体执行见 [merge-design](akshare-merge-design.md)。

---

## 3. 三方案对比账

| 维度 | A. source gateway | B. 封装 package | **C. ref 蓝本移植** |
|------|------------------|----------------|-------------------|
| 是否新建基础设施 | A.1 无需（sgw 已是）/ A.2 需胖网关 | 否 | **否**（但需修正现有 sgw/asgk 缺口） |
| §2 流量管控合规 | A.1 ✅ / A.2 ✅ | ❌ 绕过 sgw | **✅** |
| 依赖体积 | ✅ | ❌ 引入 curl_cffi | **按需显式声明**（非零新增） |
| 并发安全（100~1000 agent） | ✅ | ❌ 直连封 IP | **✅** |
| 业务逻辑归属 | 网关（A.2）或客户端 | akshare 内 | **asgk 客户端** |
| 上游同步 | 不适用 | 自动（但脆弱） | **手动按需** |
| 与 asgk 现有架构一致 | 部分 | 否 | **方向一致**（须补缺口） |
| 长期维护成本 | A.1 低 / A.2 高 | 高（上游 break） | **低** |

**结论**：**方案 C 胜出**。方案 A 在语义上已被 sgw 覆盖（无需新建），方案 B 违反核心约束。

---

## 4. "sgw 是否已是 akshare 的 source gateway"

回答用户原问题——

**sgw 已提供 source-gateway 机制**，akshare 东财源后缀命中其准入闸门。**但"已覆盖"是误判**：当前 sgw 是"后缀准入 + exact-host 精确归组"双层模型，inventory 涉及的多个东财子域（`emweb`/`datacenter`/编号 push2）**尚未在 config 精确归组**，直接请求会返回 `400 domain not proxied`。

具体哪些 host 缺失、哪些接口受影响，见 [merge-design §4 inventory](akshare-merge-design.md) 的 `gateway_readiness` 列（host-missing / header-missing / direct）。本文不再维护第二张 host 覆盖表。

> 所以"做成 source gateway"这件事 sgw 机制上已具备，但**配置/能力上有缺口**。阶段2 补齐后，剩下的工作才是把 akshare 业务函数（端点+参数+字段映射+分页）移植到 asgk。

---

## 5. 已知前置缺口（阶段2，非可选）

> 逐项细化见 [merge-design §3 阶段2](akshare-merge-design.md)；此处只列清单。

1. **host 精确归组**：补 config 或决策 wildcard/后缀到 group 映射（含编号 push2 子域）。
2. **请求头白名单透传**：`User-Agent`/`Referer`/`Cookie`/`X-CSRF-Token`/`Accept`，禁 hop-by-hop；影响响应的 header 须进 cache key。
3. **query-aware canonical cache key**：消除不同股票/日期/页串缓存。
4. **`_datacenter` 全量分页**：`all_pages`/`max_pages`，处理 `result.pages`/空/部分页失败。
5. **通用/分源请求客户端边界**：`em_get` 当前只服务东财/同花顺（`em_proxy.py:18`）。交易所/乐咕经网关则泛化，否则直连——待 [merge-design §7 决策10](akshare-merge-design.md) 风控验证。
6. **GET-only 边界**：当前候选全 GET；POST 留 P2。

> 乐咕、交易所是否进网关，已用**最保守风控策略真机验证**（单发间隔 ≥10s，禁压力测试）：深交所 3 次/乐咕 2 次均 HTTP 200 无封禁（merge-design §7 决策10，2026-07-31）。裁决：乐咕直连+自律限流，深交所经网关 exchange 组。生产加压需小步观察。

---

## 6. 最终结论

### 是否做成 source gateway？
sgw 机制上已具备，但需补 host 归组/header/cache-key 缺口。**不新建网关**。

### 是否封装 akshare package？
**不可行**。首要否决理由是违反 §2（绕过 sgw 直连东财，并发封 IP）；其次无请求 hook；再次引入 curl_cffi。

### 推荐方案
**方案 C：akshare 作 ref 蓝本，按固定 snapshot 和权威 inventory 逐接口移植到 asgk**，并在移植前修正 sgw host/header/cache-key 与 `_datacenter` 分页。具体执行计划与接口清单见 [akshare-merge-design.md](akshare-merge-design.md)。

### 一句话总结

> **不建新网关、不封装 akshare package；按固定 snapshot 的权威 inventory 把选定业务逻辑移植到 asgk，移植前先补齐 sgw host/header/cache-key 与 datacenter 分页的正确性缺口。**
