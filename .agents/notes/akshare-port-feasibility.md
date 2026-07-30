# 方案 C 可行性探索：akshare 移植到 asgk 的技术模式与证据

> **状态**：技术可行性分析（非执行计划）
> **分支**：`feat/akshare-merge`
> **最后修订**：2026-07-31
> **关联**：
> - [akshare-merge-design.md](akshare-merge-design.md)：**唯一执行计划与权威 interface inventory**（本文只引用其 ID）
> - [akshare-integration-analysis.md](akshare-integration-analysis.md)：架构方案比较
> **职责**：本文只描述"各技术获取/解析模式能否移植、需哪些 helper、有哪些已知缺口"。接口范围、阶段排序、参数签名见 merge-design。
> **参考基线**：akshare 1.18.64 / commit `fcdbf25`（见 [merge-design §0](akshare-merge-design.md)）。所有 akshare 源码事实仅针对此 snapshot，不外推到上游最新版。

---

## 0. 问题

方案 C（akshare 作 ref 蓝本，移植到 asgk）技术上是否可行？是否存在**无法或难以移植**的获取/解析模式？如存在，asgk 需怎样升级？

本文基于 akshare 固定 snapshot 的源码模式核查给出结论。**这是静态分析**，未做真实网络/并发/许可证验证——见 §5 终极结论的限定。

---

## 1. akshare 接口按"获取+解析模式"分类

> 旧版曾写"~50%/~20%/~10%"等比例，但未附可复现扫描脚本和去重规则，**已删除**。如需量化，须固定扫描目录、文件 glob、去重口径和 snapshot commit 后才能给出。

### 1.1 数据获取模式（观察到的类别）

| 获取模式 | 示例（snapshot 证据） | asgk 兼容性 |
|---|---|---|
| 东财 datacenter-web GET | `stock_repurchase_em.py:21` (`datacenter-web.eastmoney.com/api/data/v1/get`) | 可复用 `_datacenter()`（须补分页） |
| 东财 **securities** datacenter GET | `stock_yjyg_em.py:144` (`datacenter.eastmoney.com/securities/api/data/v1/get`) | **不能直接复用** `_datacenter()`（host/path 不同），需新 helper 或验证 reportName 跨端点等价 |
| 东财 **emweb** GET（F10 股东） | `stock_gdfx_em.py:465` (`emweb.securities.eastmoney.com/PC_HSF10/...`) | 非 datacenter，需 `em_get` 直调 |
| 东财 push2his GET（K线） | `stock_cyq_em.py:223` (`push2his.eastmoney.com/api/qt/stock/kline/get`) | 可复用 `em_get`，但响应经本地 JS 计算 |
| 东财 **编号 push2** GET（板块 clist） | `stock_board_concept_em.py:444` (`29.push2.eastmoney.com/api/qt/clist/get`) | 需决策编号 host 归组策略 |
| 同花顺 GET + hexin-v 签名 | `data.10jqka.com.cn` | 需本地签名 + HTML 解析 |
| 乐咕 GET + JS-MD5 token + CSRF | `stock_a_pe_and_pb.py:335` (`legulegu.com`) | 需签名 + CSRF cookie |
| eNiu GET（纯 JSON） | `stock_a_indicator.py:69` (`eniu.com/chart/...`) | 无 token/CSRF/Referer，直连即可（注：snapshot 中是**港股**指标） |
| 深交所 ShowReport GET + xlsx | `stock_margin_szse.py:102` (`www.szse.cn/api/report/ShowReport`) | 需 Referer + xlsx 解析 |
| 巨潮/东财热度 POST | （不在当前 inventory 范围） | sgw GET-only，P2 按需 |

### 1.2 响应解析模式

| 解析模式 | akshare 用法 | asgk 兼容性 |
|---|---|---|
| 纯 JSON | `r.json()["result"]["data"]` | ✅ 直接复用 |
| HTML 表格 | `BeautifulSoup` + lxml | ⚠️ 用已声明的 lxml（不引入 bs4） |
| JS 算法执行（mini-racer） | 三种子场景，见 §2.3 | ⚠️ 需区分 |
| xlsx bytes（openpyxl） | 深交所 ShowReport | ⚠️ openpyxl **当前未装**，需显式声明 |

---

## 2. mini-racer 用法拆解（关键）

核查 snapshot 中 mini-racer 的使用点，分为**三种本质不同的子场景**：

| 子场景 | 用例（snapshot 证据） | 本质 | asgk 应对 |
|--------|------|------|----------|
| **(a) JS 算请求签名 token** | 乐咕 `hash_code`（`stock_a_pe_and_pb.py:17-319`，JS 版 MD5，`py_mini_racer` 执行 `hex(date)`）；同花顺 `hexin-v` | JS 函数 → 可纯 Python 重写或 vendor JS 执行 | ⚠️ vendor JS 或 Python 重写（见 §3.1） |
| **(b) JS 实现业务算法** | 东财筹码 CYQ（`stock_cyq_em.py:27-218` `CYQCalculator`） | 纯算法，JS 与 Python 等价 | ⚠️ 翻译成 Python 或 vendor JS |
| **(c) JS 解密加密响应** | （snapshot 已扫描的 A 股模块中**未发现**） | 真正需要 JS 引擎解密 | 🔴 本 snapshot 范围内不存在 |

### 2.1 子场景 (c) 不存在的限定

> 旧版曾写"核查 akshare 全部 A 股模块，子场景 (c) 不存在，这是方案 C 可行的决定性证据"。该表述**过强**：本次为静态源码扫描，未覆盖上游所有版本，也未做真机验证。准确表述是：

**在本次扫描的 A 股目标模块范围内、固定 snapshot `fcdbf25` 中，未发现"JS 解密加密响应"的模式；所有 mini-racer 用法都是签名 (a) 或算法 (b)。** 此结论不外推到整个 akshare 或未来 snapshot。

### 2.2 乐咕 token 算法的事实修正

> 旧版笼统称"乐咕请求需 JS 签名"。核查发现 snapshot 中存在**多种**乐咕 token 协议，不能合并：

- `stock_market_pe_lg` / `stock_market_pb_lg`（`stock_a_pe_and_pb.py`）：token = **JS 版 MD5**（内联 `hash_code`，`py_mini_racer` 执行 `hex(date)`）+ CSRF cookie（`get_cookie_csrf` 取 `<meta name="_csrf">`，以 `X-CSRF-Token` 头 + cookie 请求）。对应 AKP-VAL-001/002。
- 其他乐咕接口（buffett/congestion/all_pb/ttm 等）：token = **纯 Python `hashlib.md5`**（`get_token_lg`，`stock_a_indicator.py:40-51`）+ CSRF。
- `stock_hk_indicator_eniu`（`stock_a_indicator.py:54`）：**完全无需 token/CSRF/Referer**，仅通用 UA（注意是港股）。

→ 设计上**不能**统一成单个 `legu_token()` 函数。每个 endpoint 须在 inventory 单独记录 token 算法、CSRF、Referer、是否需 session。

### 2.3 CYQ 筹码算法

`stock_cyq_em`（AKP-CHIP-001）：服务端只返回 K 线，筹码分布由本地 `py_mini_racer` 执行 `CYQCalculator`（`stock_cyq_em.py:27-218`）计算。这是**业务算法**（子场景 b），非响应解密。**决策已定：vendor akshare JS 用 py_mini_racer 执行（方案 A），不 Python 重写**（[merge-design §7 决策8](akshare-merge-design.md)）。CYQ 是纯数学零 DOM 依赖，py_mini_racer 可直接跑；代价是须显式声明 py-mini-racer 直接依赖 + 阶段4 并发安全测试（thread-local/锁）。

---

## 3. asgk 技术升级（按技术模式，非按接口）

> 这些是"技术能力"，**有 approved inventory 消费者时才落地**。是否扩展 `@source` 元数据见 §3.5（结论：不扩展）。

### 3.1 本地签名工具 `_signing.py`

**触发条件**：inventory 批准了需 JS 签名的接口（如同花顺 `hexin-v`、乐咕 JS-MD5 token）。

**依赖与约束**：
- `py-mini-racer` 当前是 mootdx 传递依赖；asgk 直接 import 时**必须提升为直接依赖**（`pyproject.toml`）。
- vendor JS 文件须记录：upstream repo / snapshot commit / source path / LICENSE / local hash / sync policy。

**示例（修正版，旧版有错）**：

```python
# asgk/_signing.py
"""本地签名工具：vendor akshare 的 JS，用 py-mini-racer 执行。

py-mini-racer 若被 asgk 直接 import，须在 pyproject.toml 显式声明为直接依赖。
JS 文件 vendor 在 asgk/_vendor/，每个文件头注明来源/commit/license/hash。
"""
from functools import lru_cache
from pathlib import Path
import py_mini_racer

_VENDOR_DIR = Path(__file__).parent / "_vendor"

# 旧版用 maxsize=1 是错的：多个 JS 文件（ths.js / legu_hash.js）会互相驱逐、反复 eval。
@lru_cache(maxsize=None)
def _engine(js_name: str) -> "py_mini_racer.MiniRacer":
    """加载并缓存 JS 引擎（首次 eval ~50ms，后续调用 <1ms）。"""
    js = py_mini_racer.MiniRacer()
    js.eval((_VENDOR_DIR / js_name).read_text(encoding="utf-8"))
    return js

def ths_hexin_v() -> str:
    return _engine("ths.js").call("v")

def legu_market_pe_token(date_iso: str | None = None) -> str:
    """乐咕 market-pe/pb token（JS 版 MD5）。

    注意：仅适用于 stock_market_pe_lg/pb_lg；其他乐咕接口用纯 Python md5，不在此。
    """
    from datetime import datetime
    d = date_iso or datetime.now().date().isoformat()
    return _engine("legu_hash.js").call("hex", d).lower()
```

**并发安全（关键）**：目标部署是 100–1000 agent 并发。MiniRacer context 的线程安全性须先验证；若不安全，用 thread-local context 或锁保护。阶段4 须配并发测试。

### 3.2 HTML 表格解析 `_htmltable.py`

**触发条件**：inventory 批准了 HTML 表格接口（如新浪龙虎榜、乐咕赚钱效应、同花顺部分接口）。

**依赖**：`lxml`（asgk 已直接声明，此前未真用，从此开始真用）。不引入 bs4。

**示例（修正版，旧版 `etree._Element.text_content()` 不存在是错的）**：

```python
# asgk/_htmltable.py
"""HTML 表格解析（基于 lxml，已声明依赖）。

etree.HTML() 返回 lxml.etree._Element，没有 text_content()——那是 lxml.html.HtmlElement 的方法。
"""
# 方式一：用 lxml.html（有 text_content()）
from lxml import html

def parse_html_tables_v1(content: str) -> list[list[list[str]]]:
    tree = html.fromstring(content)
    tables = []
    for table in tree.xpath("//table"):
        rows = []
        for row in table.xpath(".//tr"):
            cells = [c.text_content().strip()
                     for c in row.xpath(".//td|.//th")]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables

# 方式二：保持 etree，用 itertext()
from lxml import etree

def parse_html_tables_v2(content: str) -> list[list[list[str]]]:
    tree = etree.HTML(content)
    tables = []
    for table in tree.xpath("//table"):
        rows = []
        for row in table.xpath(".//tr"):
            cells = ["".join(c.itertext()).strip()
                     for c in row.xpath(".//td|.//th")]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables
```

**测试边界**（须覆盖，不能只测 happy path）：malformed HTML、空表、`rowspan`/`colspan`（简单 list[dict] parser 可能不足以替代 `pandas.read_html`，须评估）、嵌套标签、编码检测。

### 3.3 xlsx 流解析 `_xlsx.py`

**触发条件**：inventory 批准了深交所 ShowReport 系列（AKP-FAILOVER-001 及同模式接口）。

**依赖（关键修正）**：旧版称"openpyxl 3.1.5 已装（pandas 传递依赖），零新依赖"——**错误**。openpyxl 当前**不在** asgk lockfile（pandas 把它列为 `excel` optional extra，非默认依赖）。采用 xlsx 方案须：
- `pandas` 提升为 asgk 直接依赖（当前是 mootdx→tdxpy 传递依赖）；
- `openpyxl` **新增**直接依赖；
- 更新 lockfile；
- clean environment 安装测试（不能依赖开发机偶然状态）。

也可选择不依赖 pandas，直接用 openpyxl 解析，减少一层依赖。

**示例**：

```python
# asgk/_xlsx.py
"""xlsx 二进制流解析（深交所 ShowReport 系列用）。

openpyxl 当前未安装，采用本模块须在 pyproject.toml 显式声明 openpyxl（及 pandas，若用 pd.read_excel）。
"""
from io import BytesIO
import pandas as pd  # 若用此路径，pandas 须为直接依赖

def parse_xlsx(content: bytes, dtype: dict | None = None) -> list[dict]:
    """HTTP 响应 xlsx bytes → list[dict]。"""
    df = pd.read_excel(BytesIO(content), engine="openpyxl", dtype=dtype)
    return df.to_dict("records")
```

**使用**（AKP-FAILOVER-001，深交所融资融券明细）：

```python
@source(tier="S", via="gateway")
def margin_detail_szse(date: str) -> list[dict]:
    r = em_get("https://www.szse.cn/api/report/ShowReport",
               params={"SHOWTYPE": "xlsx", "CATALOGID": "1837_xxpl",
                       "TABKEY": "tab2", "tab2PAGENO": "1",
                       "txtDate": f"{date[:4]}-{date[4:6]}-{date[6:]}"},
               headers={"Referer": "https://www.szse.cn/disclosure/margin/margin/index.html"})
    return parse_xlsx(r.content, dtype={"证券代码": str})  # 保前导零
```

**测试边界**：表头偏移、空行、证券代码前导零（`dtype=str`）、多 sheet、空结果。

> 注意：`Referer` 当前**到不了上游**（sgw 不透传 headers，见 [integration-analysis §1.2](akshare-integration-analysis.md)）。此接口依赖阶段2 的 header 白名单透传完成。

### 3.4 `.xls`（老 BIFF）格式的事实修正

> 旧版称"xlrd 2.0+ 默认不支持 .xls，需 xlrd<2.0"——**事实错误**。xlrd 2.x 移除的是 **`.xlsx`** 支持，`.xls`（老 BIFF）**仍支持**。

snapshot 中真正的 `.xls` 静态文件下载仅 `stock_industry_clf_hist_sw`（申万行业分类，`swsresearch.com/.../StockClassifyUse_stock.xls`）。处理方式（[merge-design 未列入候选](akshare-merge-design.md)，按需）：① 装 xlrd（支持 .xls）；② 改用东财源拿申万行业；③ 跳过。倾向②（零额外依赖）。

### 3.5 `@source sign/parse` 字段：**不扩展**

旧版建议给 `@source` 加 `sign`/`parse` 元数据字段。**结论：不扩展**——当前无明确消费者（无 registry 导出、无文档生成器）。`@source` 是**纯声明元数据，不驱动行为**：实际 tier/via 仍由函数体内 `em_get(..., tier=...)` 决定。若 `@source(tier="L")` 而内部默认 S，运行时仍是 S。验收须同时校验装饰器声明与运行时 `X-Cache-Tier` 一致。除非将来出现明确的元数据消费者，否则不扩大契约面。

---

## 4. 模式级可移植性裁决

> 旧版有一张逐接口裁决表，与 merge-design inventory 重复且冲突，**已删除**。此处只给**模式级**裁决，接口级归属见 inventory。

| 技术模式 | 可否移植 | 必要 helper / 前置 | 已知缺口 | inventory 示例 |
|---|---|---|---|---|
| datacenter-web JSON（多页） | ✅ | `_datacenter` 补 all_pages | 分页只取首页 | AKP-HOLD-003/004, EVT, RISK |
| **securities** datacenter JSON | ⚠️ | 新 helper 或验证跨端点等价 | host/path 不同 | AKP-EARN-001/002 |
| emweb F10 JSON（非 datacenter） | ✅ | `em_get` 直调 | host 未归组 | AKP-HOLD-001/002 |
| push2his K线 + 本地算法 | ✅ | CYQ Python/vendor + mini-racer | 线程安全 | AKP-CHIP-001 |
| 编号 push2 clist JSON | ✅ | 通用 push2 helper | 主用无编号 host（asgk 已验证），编号作备选 | AKP-BOARD-001 |
| 同花顺签名 + HTML | ✅ | `_signing` + `_htmltable` | header 透传 | （按需，P2） |
| 乐咕 JS-MD5 token + CSRF | ✅ | `_signing`(legu_market_pe_token) + CSRF | 直连/网关待风控验证 | AKP-VAL-001/002 |
| eNiu 纯 JSON | ✅ | 直连 | （snapshot 中是港股） | — |
| 深交所 ShowReport xlsx | ✅ | `_xlsx` + Referer 透传 | openpyxl 未装 + header 未透传 + 风控待验证 | AKP-FAILOVER-001 |
| POST | ⏸️ | sgw 扩展 POST | GET-only | （P2 按需） |
| curl_cffi / JA3 | ⏸️ | — | asgk 走网关无需；有替代源 | （P2 按需） |

---

## 5. 终极可行性结论（有条件）

### 5.1 方案 C 可行吗？

**静态分析未发现原理性阻塞**。在固定 snapshot `fcdbf25` 已扫描的 A 股目标模块中：
- mini-racer 只用于签名 (a) 和算法 (b)，未发现响应解密 (c)；
- 所有技术模式都有对应 helper 或前置修正路径。

**但"可移植"≠"零改造"**。以下必须先完成（[merge-design 阶段2](akshare-merge-design.md)）：
- sgw host 精确归组（含编号 push2）；
- 请求头白名单透传；
- query-aware canonical cache key；
- `_datacenter` 全量分页；
- 直接依赖显式声明（pandas/mini-racer/openpyxl）。

且静态分析不能证明：endpoint 当前在线、签名算法当前有效、经 sgw 后 headers 正确、mini-racer 在目标 Python/平台兼容、100–1000 agent 并发稳定、vendor JS 许可证合规、字段 schema 未漂移。**须由 go/no-go spike 真机验证**（每类协议至少一个真实请求 + 经 sgw 端到端 + 并发测试 + 许可证审查 + schema fixture）。

> 旧版"全部难点接口 100% 可移植""P0 100% 纯 JSON 零难点""三个模块纯新增零破坏"等**绝对结论已删除**。

### 5.2 asgk 设计需要升级吗？

需要，但按技术模式按需落地（有 inventory 消费者才建），且**不是零新增依赖**：
- `_signing.py`（vendor JS + mini-racer；mini-racer 须显式声明直接依赖）
- `_htmltable.py`（lxml，已声明）
- `_xlsx.py`（openpyxl + pandas，**新增直接依赖**）
- `_dataframe.py`（`to_df`，pandas 须显式声明直接依赖）——见 §6
- `_datacenter` 扩展 all_pages（阶段2）
- `@source sign/parse`：**不扩展**（§3.5）

### 5.3 依赖原则（替换旧版错误的"零重依赖"）

- ✅ 风控源经 sgw（§2 合规，不可妥协）
- ✅ asgk 直接 import 的第三方包**显式声明**为直接依赖（不依赖传递依赖长期存在）
- ✅ 表格型新接口默认返回 `list[dict]`（调用方友好 + 跨版本稳定）
- 🔴 拒绝 curl_cffi（反反爬对抗走网关层）

---

## 6. 返回类型契约（修正）

> 旧版称"asgk 现有 50 个函数全部返回 `list[dict]`"——**不准确**。现有公共函数返回类型包括 `dict`、`dict[str,dict]`、`str`、`float`（如 `forward_pe` 返回 float、`tencent_quote` 返回 dict）。

**契约**：**表格型新接口**默认返回 `list[dict]`；单对象、聚合结果或纯计算接口按领域自然返回（`dict`/`str`/`float`）。返回类型逐项写入 inventory 的 `output_type`。

**`to_df()` 可选**：调用方完全可以直接 `pandas.DataFrame(records)`，是否有必要为五行包装扩展顶级 API 待评审。若提供，`to_df()` 只接受 `Sequence[Mapping]`，不适用于 dict/标量/字符串接口。pandas 须为直接依赖。

```python
# asgk/_dataframe.py（可选）
import pandas as pd  # 须为直接依赖

def to_df(records):
    """list[dict] → DataFrame。仅适用于表格型接口。"""
    return pd.DataFrame(records)
```

---

## 7. 决策同步

> 评审已定的技术决策（细节见 [merge-design §7](akshare-merge-design.md)）：
> - vendor JS + py_mini_racer 执行（mini-racer 须显式声明直接依赖）
> - 表格型接口 list[dict]，`to_df` 可选
> - 显式声明直接依赖（不依赖传递依赖）
> - **CYQ 实现 = vendor JS（方案 A）**：vendor akshare CYQ JS 用 py_mini_racer 执行，不 Python 重写（merge-design §7 决策8）
> - **vendor JS 同步 = CI 定期 diff（方案 B）**：锁 snapshot，CI diff 上游 ths.js/CYQ JS 告警（merge-design §7 决策9）；当前 inventory 只用 CYQ，ths.js 推迟 P2
> - **akshare 仓库 = submodule**：`ref/akshare` submodule 固定 commit `fcdbf25`，保留上游 MIT LICENSE（merge-design §7 决策6、§9）
> - `@source sign/parse` **不扩展**（§3.5）

> 实施前待真机验证（merge-design §7 决策10/11）→ **均已完成（2026-07-31）**：
> - **乐咕/交易所风控 → 通过**：深交所 3 次（间隔10s）均 200；乐咕 2 次（间隔10s）均 200，无封禁。裁决：乐咕直连+自律限流，深交所经网关 exchange 组。
> - **datacenter reportName 互通 → 互通**：datacenter-web + 业绩 reportName + 等值 filter + source=WEB 返回 200 页。AKP-EARN 复用 `_datacenter()`，不需新 helper。filter 是等值 `(REPORT_DATE='2024-09-30')` 非前缀 `^"..."`。

> 本文只保留**开放技术问题**：
> - mini-racer 线程安全验证（CYQ vendor 后须 thread-local/锁 + 并发测试）
> - `_htmltable` 对 rowspan/colspan 的覆盖评估
> - `_xlsx` 是否走 openpyxl 直接解析（不经 pandas）以减依赖
