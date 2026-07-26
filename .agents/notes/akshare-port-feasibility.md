# 方案 C 可行性探索：akshare 移植到 asgk 的难点与 asgk 设计升级

> **状态**：Draft，可行性探索（非执行计划）
> **分支**：`feat/akshare-merge`
> **日期**：2026-07-26
> **关联**：
> - [akshare-integration-analysis.md](akshare-integration-analysis.md)（已确立选方案 C）
> - [akshare-merge-design.md](akshare-merge-design.md)（合成方案执行 plan）

---

## 0. 问题

方案 C（akshare 作 ref 蓝本，移植解析逻辑到 asgk）是否真的可行？是否存在**无法或难以移植**的接口？如果存在，asgk 现有设计需要怎样升级才能消化？

本文基于 akshare 全量源码的依赖使用点核查 + 解析模式分类，给出结论。

---

## 1. akshare 接口按"获取+解析模式"分类

核查 akshare 所有 A 股相关模块，按**数据获取方式 × 响应解析方式**两维度分类：

### 1.1 数据获取方式（4 类）

| 获取方式 | 占比 | asgk 兼容性 |
|---------|------|------------|
| **东财 datacenter GET**（`datacenter-web.eastmoney.com/api/data/v1/get`） | ~50% | ✅ 完全兼容，复用 `_datacenter()` |
| **东财 push2 GET**（`push2his.eastmoney.com` / `push2.eastmoney.com`） | ~20% | ✅ 完全兼容，复用 `em_get()` |
| **同花顺 GET + hexin-v 签名**（`data.10jqka.com.cn` / `q.10jqka.com.cn`） | ~10% | ⚠️ 需本地 JS 签名（asgk 已有先例） |
| **乐咕/eNiu GET + token 签名**（`legulegu.com` / `eniu.com`） | ~5% | ⚠️ 需本地 JS 签名 + HTML 拿 CSRF |
| 其他（新浪 HTML / 巨潮 POST / 交易所 GET / 雪球） | ~15% | 多数兼容，个别需特殊处理 |

### 1.2 响应解析方式（4 类）

| 解析方式 | akshare 用法 | asgk 兼容性 |
|---------|------------|------------|
| **纯 JSON** | `r.json()["result"]["data"]` | ✅ 直接复用，返回 `list[dict]` |
| **HTML + lxml/bs4** | `BeautifulSoup(r.text).find(...)` | ⚠️ 需引入轻量 HTML 解析（见 §3.2） |
| **JS 算法执行（mini-racer）** | 三种子场景，见 §2.2 | ⚠️ 需区分对待 |
| **Excel（openpyxl/xlrd）** | 交易所下载 Excel 解析 | ❌ 个别接口，建议跳过 |

---

## 2. 难点接口清单与根因分析

### 2.1 P0 接口（11 个）——**全部可移植，零难点**

逐一核查 P0 候选接口的解析模式：

| 接口 | 获取 | 解析 | 难度 |
|------|------|------|------|
| 十大股东 `stock_gdfx_top_10_em` | 东财 datacenter | 纯 JSON | 🟢 极易 |
| 股东持股变化 `stock_gdfx_holding_change_em` | 东财 datacenter | 纯 JSON | 🟢 极易 |
| 业绩预告 `stock_yjyg_em` | 东财 datacenter | 纯 JSON | 🟢 极易 |
| 业绩快报 `stock_yjkb_em` | 东财 datacenter | 纯 JSON | 🟢 极易 |
| 回购 `stock_repurchase_em` | 东财 datacenter | 纯 JSON | 🟢 极易 |
| 高管增减持 `stock_hold_management_detail_em` | 东财 datacenter | 纯 JSON | 🟢 极易 |
| 机构调研 `stock_jgdy_detail_em` | 东财 datacenter | 纯 JSON | 🟢 极易 |
| 概念板块成份股 `stock_board_concept_cons_em` | 东财 push2 | 纯 JSON | 🟢 极易 |
| 行业板块成份股 `stock_board_industry_cons_em` | 东财 push2 | 纯 JSON | 🟢 极易 |
| 股权质押 `stock_gpzy_pledge_ratio_em` | 东财 datacenter | 纯 JSON | 🟢 极易 |
| 商誉 `stock_sy_em` | 东财 datacenter | 纯 JSON | 🟢 极易 |

**结论**：P0 全部是东财 datacenter/push2 的纯 JSON 接口，与 asgk 现有 `capital.py`/`signal.py` 范式**完全同构**，移植工作量极低（每接口 ~30-50 行）。

### 2.2 P1 难点接口（mini-racer + HTML）

这些是真正需要设计升级才能消化的接口：

| 接口 | 难点 | 根因 |
|------|------|------|
| **个股 PE/PB 分位** `stock_a_indicator_lg` | HTML + CSRF | eNiu 需先 GET HTML 拿 `_csrf` token，再带 token 请求 JSON |
| **全市场 PE/PB** `stock_market_pe_lg` | mini-racer | 乐咕请求需 `hex(date)` 签名 token |
| **筹码分布** `stock_cyq_em` | mini-racer | CYQ 算法在 JS 里实现（不是解密，是计算） |
| **同花顺资金流** `stock_fund_flow_individual` | mini-racer + HTML | 同花顺 `hexin-v` cookie 签名 + HTML 表格解析 |
| **同花顺概念板块** `stock_board_concept_ths` | mini-racer + HTML | 同上 |
| **同花顺技术选股** `stock_rank_*_ths`（11 个） | mini-racer + HTML | 同上 |
| **新浪龙虎榜** `stock_lhb_detail_daily_sina` | HTML | 新浪返回 HTML 表格 |
| **乐咕赚钱效应** `stock_market_activity_legu` | HTML | 乐咕返回 HTML |

### 2.3 mini-racer 的三种子场景（关键拆解）

核查所有 mini-racer 使用点，发现**三种本质不同的用法**，难度天差地别：

| 子场景 | 用例 | 本质 | asgk 应对 |
|--------|------|------|----------|
| **(a) JS 算请求签名 token** | 乐咕 `hex(date)`、同花顺 `hexin-v` | JS 函数 → 纯 Python 重写（几行） | 🟢 已有先例：`cls_telegraph` 的 `md5(sha1(qs))` |
| **(b) JS 实现业务算法** | 东财筹码 CYQ | 纯算法，JS 与 Python 等价表达 | 🟢 翻译成 Python（公开算法） |
| **(c) JS 解密加密响应** | （akshare A 股里**未发现**） | 真正需要 JS 引擎 | 🔴 本项目范围内**不存在** |

**关键发现**：核查 akshare 全部 A 股模块，**子场景 (c) 不存在**。所有 mini-racer 用法都是 (a) 签名或 (b) 算法，都可以纯 Python 重写。这是方案 C 可行的决定性证据。

### 2.4 "Excel 响应"接口分类（修正：多数可移植，勿一概跳过）

> **修正（2026-07-27）**：早先版本笼统称"交易所 Excel 下载，建议跳过"。核查后发现 akshare 的 Excel 接口有**两种本质不同**的形态，其中深交所系列其实零难度可移植。混为一谈会把 `stock_margin_detail_szse`（融资融券官方容灾源）等 5+ 接口误判丢弃。

**类型 A：HTTP API 返回 xlsx 二进制流（深交所 ShowReport 系列）→ ✅ 可移植**

URL 是 `https://www.szse.cn/api/report/ShowReport`（**不是下载 .xls 文件**），通过 `SHOWTYPE=xlsx` 参数让一个普通 HTTP API 返回 xlsx 格式的 bytes 响应。GET 请求，sgw 可代理。

| 接口 | 数据 | 价值 |
|------|------|------|
| `stock_margin_detail_szse` | 融资融券明细（深交所官方） | 🔴 **东财被封时的官方容灾源**（见 integration-analysis §4） |
| `stock_margin_underlying_info_szse` | 融资融券标的名单 | 中 |
| `stock_szse_summary` / `_area_summary` / `_sector_summary` | 深交所市场总貌/地区/行业成交 | 大盘情绪 |
| `stock_info_sz_name_code` | 深市代码表 | 基础设施 |
| `stock_sgt_*_exchange_rate_szse`（2 个） | 沪深港通汇率 | 北向配套 |

**移植方式**：和 JSON 接口同构，只是 `em_get` 拿到的 `response.content`（bytes）丢给 `pd.read_excel(BytesIO(content))`。openpyxl 3.1.5 已装（pandas 传递依赖），**零新依赖**。需新增 `asgk/_xlsx.py` 工具（~10 行）。

**类型 B：真·静态 .xls 文件下载（申万行业分类）→ ⚠️ 需权衡，仅 1 个接口**

唯一接口：`stock_industry_clf_hist_sw`，URL `https://www.swsresearch.com/.../StockClassifyUse_stock.xls`，是真实的 .xls（老 BIFF 格式）静态文件。

难点：
- `.xls`（非 .xlsx）需 xlrd，但 **xlrd 2.0+ 默认不支持 .xls**（需 `xlrd<2.0` 或转 .xlsx）
- 申万研究所偶有访问不稳定

权衡选项（倾向 2）：
1. 装 `xlrd<2.0`（增加一个依赖，仅为 1 个接口）
2. **改用东财源拿申万行业**（akshare 有 `stock_industry_*_em` 替代，零依赖）
3. 跳过

**类型 C：curl_cffi JA3 绕过 → ⏸️ 暂缓（akshare 独有，非 Excel）**

`news_stock_em`（东财搜索 api）、`news_baidu` 需 TLS 指纹伪装。asgk 已有 `eastmoney_stock_news` 替代源，**P2 按需**。

**裁决**：类型 A 全部可移植（含重要的融资融券官方容灾源）；类型 B 单接口建议改源；类型 C 暂缓。**无"必须跳过"的 Excel 接口。**

---

## 3. asgk 设计升级方案（针对难点）

针对 §2.2 的难点接口，asgk 需要五处设计升级。**这些都是新增能力，不破坏现有契约**。

### 3.1 升级一：本地签名工具模块 `_signing.py`

**问题**：乐咕/同花顺/eNiu 都需要本地计算签名 token，asgk 目前只在 `cls_telegraph` 内联实现了一次。

**升级**：抽出公共签名工具模块。

**决策（评审已定）**：**vendor akshare 的 JS + py-mini-racer 执行**。

理由：py-mini-racer（48M）已是 mootdx 的传递依赖，asgk 环境里已装，**用起来零新增成本**。相比纯 Python 重写，vendor JS 的优势是上游 ths.js 变了只换文件、无需逆向重写。

```python
# asgk/_signing.py（新增）
"""本地签名工具：vendor akshare 的 JS，用 py-mini-racer 执行。

py-mini-racer 已是 mootdx 传递依赖（已装），无新增依赖成本。
JS 文件 vendor 在 asgk/_vendor/，上游变更时换文件即可。
"""
from functools import lru_cache
from pathlib import Path
import py_mini_racer

_VENDOR_DIR = Path(__file__).parent / "_vendor"

@lru_cache(maxsize=1)
def _engine(js_name: str) -> py_mini_racer.MiniRacer:
    """加载并缓存 JS 引擎（首次 eval ~50ms，后续调用 <1ms）。"""
    js = py_mini_racer.MiniRacer()
    js.eval((_VENDOR_DIR / js_name).read_text(encoding="utf-8"))
    return js

def ths_hexin_v() -> str:
    """同花顺 hexin-v cookie 签名（vendor ths.js 的 v() 函数）。"""
    return _engine("ths.js").call("v")

def legu_token(date_iso: str | None = None) -> str:
    """乐咕请求 token（vendor legulegu hash_code 的 hex() 函数）。"""
    from datetime import datetime
    d = date_iso or datetime.now().date().isoformat()
    return _engine("legu_hash.js").call("hex", d).lower()
```

**vendor 文件来源**（从 ref/akshare 拷贝）：
- `akshare/stock_feature/ths.js`（39K，989 行）→ `asgk/_vendor/ths.js`
- `akshare/stock_feature/stock_a_pe_and_pb.py` 内联的 `hash_code` 字符串 → `asgk/_vendor/legu_hash.js`

**价值**：
- 同花顺资金流/概念板块/技术选股（11+ 接口）共用 `ths_hexin_v()`
- 乐咕全市场 PE/PB/巴菲特/股债利差共用 `legu_token()`
- 上游 JS 变更时换 vendor 文件即可，无需逆向重写

**对现有设计的影响**：零破坏。纯新增模块。`cls_telegraph` 现有内联签名可保留（已是纯 Python md5/sha1），也可后续迁移到本模块（可选）。

### 3.2 升级二：HTML 解析（用已装的 lxml，不引入 bs4）

**问题**：同花顺资金流/龙虎榜（新浪）/乐咕赚钱效应返回 HTML 表格，akshare 用 `BeautifulSoup + lxml`。

**asgk 现状**：`lxml>=6.1.1` 已在 pyproject.toml 声明（虽代码里没用过，~12M 已装）。**直接用 lxml.etree，无需 bs4**（bs4 是上层封装，etree 更快且已装）。

```python
# asgk/_htmltable.py（新增）
"""HTML 表格解析（基于 lxml.etree，已装）。

用于同花顺/新浪/乐咕返回的 HTML 表格数据。
"""
from lxml import etree

def parse_html_tables(html: str) -> list[list[list[str]]]:
    """提取所有 <table> 为 list[list[list[str]]]。"""
    tree = etree.HTML(html)
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
```

**vs stdlib html.parser（之前误推的方案）**：
- lxml etree ~15 行，stdlib 版 ~30 行
- lxml 性能更快（C 实现），xpath 表达力更强
- 已装，零新增依赖

**价值**：覆盖所有 HTML 表格接口（同花顺/新浪/乐咕）。

**对现有设计的影响**：零破坏。纯新增模块。从这一刻起 asgk 实际开始用 lxml（之前只声明没用）。

### 3.3 升级三：xlsx 流解析工具 `_xlsx.py`（深交所 ShowReport 系列）

**问题**：深交所 `ShowReport` API 用 `SHOWTYPE=xlsx` 返回 xlsx bytes 流（见 §2.4 类型 A），需解析。涉及 5+ 接口，含融资融券官方容灾源。

**asgk 现状**：openpyxl 3.1.5 已装（pandas 传递依赖），pandas 也已装。**零新依赖**。

```python
# asgk/_xlsx.py（新增）
"""xlsx 二进制流解析（深交所 ShowReport 系列用）。

openpyxl 已装（pandas 传递依赖），零新依赖。
"""
from io import BytesIO
import pandas as pd

def parse_xlsx(content: bytes, dtype: dict | None = None) -> list[dict]:
    """HTTP 响应的 xlsx bytes → list[dict]。"""
    df = pd.read_excel(BytesIO(content), engine="openpyxl", dtype=dtype)
    return df.to_dict("records")
```

**使用**（深交所融资融券明细）：

```python
@source(tier="S", via="gateway")
def margin_detail_szse(date: str) -> list[dict]:
    r = em_get("https://www.szse.cn/api/report/ShowReport",
               params={"SHOWTYPE": "xlsx", "CATALOGID": "1837_xxpl",
                       "txtDate": f"{date[:4]}-{date[4:6]}-{date[6:]}", ...},
               headers={"Referer": "https://www.szse.cn/disclosure/margin/margin/index.html"})
    return parse_xlsx(r.content, dtype={"证券代码": str})
```

**价值**：覆盖深交所 ShowReport 全系列（5+ 接口），尤其补齐融资融券的**官方容灾源**（东财被封时兜底）。

**对现有设计的影响**：零破坏。纯新增模块。

### 3.4 升级四：扩展 `@source` 装饰器的 via 字段

**问题**：现有 `Via = Literal["gateway", "direct"]` 只区分"经网关"和"直连"。但难点接口需要更细的标注：

- 同花顺源经网关，但需本地签名（`gateway + sign=ths`）
- 乐咕源直连，但需本地签名 + CSRF（`direct + sign=legu`）
- 东财 push2 经网关，纯 JSON（`gateway`）

**升级（可选，向后兼容）**：

```python
# asgk/_contract.py（扩展）
Via = Literal["gateway", "direct"]

@dataclass
class SourceMeta:
    tier: Tier
    via: Via
    sign: str | None = None  # 新增：签名类型 "ths" / "legu" / "cls" / None
    parse: str | None = None  # 新增：解析类型 "json" / "html_table" / "text"
    cli: str | None = None
    ...
```

**注意**：这是**可选升级**，目的是让 `@source` 元数据更完整（驱动文档生成/离线分析）。现有函数不挂 `sign`/`parse` 也能工作（默认 None）。

**对现有设计的影响**：向后兼容。现有 50 个函数无需改动；新函数可选挂 `sign`/`parse`。

### 3.5 升级五：sgw 新增乐咕/交易所限流组（可选）

**问题**：乐咕（`legulegu.com`/`eniu.com`）和交易所（`query.sse.com.cn`）不在 sgw 现有 `PROXIED_DOMAIN_SUFFIXES` 里。

**两个选项**（已在 integration-analysis.md §5 讨论）：

| 选项 | 做法 | 推荐 |
|------|------|------|
| asgk 内直连 + 自律限流 | 复用 `em_proxy._direct_throttle` 模式 | 🟢 乐咕（无封 IP 风险） |
| sgw 新增限流组 | config 加 `legu`/`exchange` 组 | 🟡 交易所源（与东财隔离更安全） |

**对现有设计的影响**：sgw config 扩展，不改代码。零破坏。

---

## 4. 难点接口的移植可行性裁决

逐个裁决 §2.2（签名/HTML 难点）+ §2.4 类型 A（xlsx 流）的接口，确认是否可移植：

| 接口 | 升级依赖 | 裁决 | 工作量 |
|------|---------|------|--------|
| 个股 PE/PB 分位（eNiu） | 升级一（CSRF）+ HTML 拿 token | ✅ 可移植 | 中（~80 行） |
| 全市场 PE/PB（乐咕） | 升级一（`legu_token`） | ✅ 可移植 | 低（~40 行） |
| 巴菲特指标（乐咕） | 升级一（`legu_token`） | ✅ 可移植 | 低（~30 行） |
| 股债利差（乐咕） | 升级一（`legu_token`） | ✅ 可移植 | 低（~30 行） |
| 筹码分布（东财） | 升级一无关，CYQ 算法 Python 重写 | ✅ 可移植 | 中（~100 行，含 CYQ 算法） |
| 同花顺资金流 | 升级一（`ths_hexin_v`）+ 升级二（HTML） | ✅ 可移植 | 中（~80 行） |
| 同花顺概念板块 | 升级一 + 升级二 | ✅ 可移植 | 中（~60 行） |
| 同花顺技术选股（11 个） | 升级一 + 升级二 | ✅ 可移植 | 中（~200 行，11 接口共用基建） |
| 新浪龙虎榜 | 升级二（HTML） | ✅ 可移植 | 低（~50 行） |
| 乐咕赚钱效应 | 升级二（HTML） | ✅ 可移植 | 低（~50 行） |
| **深交所融资融券明细**（官方容灾源） | 升级三（xlsx 流） | ✅ 可移植 | 低（~40 行） |
| **深交所市场总貌/代码表/汇率**（4 个） | 升级三（xlsx 流） | ✅ 可移植 | 低（~80 行，4 接口共用） |

**裁决结论**：**全部难点接口可移植**，无"无法移植"项。需要的前置升级是 §3.1 + §3.2 + §3.3（三个轻量新模块），都是纯新增、零破坏。

> **修正（2026-07-27）**：早先裁决表漏列了深交所 xlsx 系列接口（5 个，含融资融券官方容灾源），现补齐。详见 §2.4 类型 A。

---

## 5. asgk 设计升级汇总

### 5.1 依赖事实修正（重要）

**前期分析的"零重依赖"前提是错的。** 核查 asgk 的 `uv.lock` 真实闭包：

| 依赖 | 体积 | asgk 现状 | 性质 |
|------|------|----------|------|
| pandas | 50M | ✅ 已装（mootdx 传递） | 可用 |
| numpy | 33M | ✅ 已装（pandas 传递） | 可用 |
| py-mini-racer | 48M | ✅ 已装（mootdx 传递） | 可用 |
| lxml | 12M | ✅ 已装（asgk 直接声明） | 可用（之前声明但没用） |
| requests | 480K | ✅ 已装（asgk 直接） | 已用 |
| mootdx | ~100K | ✅ 已装（asgk 直接） | 已用（TCP 行情） |

**asgk 真实安装闭包 ~160MB+**，不是早期误称的 ~5MB。pandas/numpy/mini-racer 都是 mootdx 拉进来的——akshare 的"重依赖"大部分 asgk 早就在了。

**真正值得拒绝的依赖**（有独立工程代价、非 mootdx 拉入）：
- `curl_cffi` (31M)：TLS 指纹绕过工具，本质反反爬对抗；asgk 已有 em_get 走网关，不需要每客户端带 JA3 伪装
- `akshare` 全量包：400+ 接口直连绕过 sgw，违反 §2

**修正后的设计原则**（替换原来错误的"零重依赖"）：
- ✅ 风控源经 sgw 网关（§2 合规，这是真正不可妥协的）
- ✅ 实现层可自由用 pandas/lxml/mini-racer（反正已装）
- ✅ 契约层返回 `list[dict]`（理由：调用方友好 + 跨版本稳定，**不是**"零依赖"）
- ✅ 拒绝 curl_cffi（避免反反爬对抗，走网关层解决）

### 5.2 升级清单（评审已定方案）

| 升级 | 类型 | 依赖变化 | 破坏性 | 必要性 |
|------|------|---------|--------|--------|
| `_signing.py` vendor JS + mini-racer | 新增模块 | 零（mini-racer 已装） | 无 | 必需（覆盖乐咕+同花顺 15+ 接口） |
| `_htmltable.py` 用 lxml.etree | 新增模块 | 零（lxml 已声明） | 无 | 必需（覆盖 HTML 接口） |
| `_xlsx.py` 深交所 ShowReport 流解析 | 新增模块 | 零（openpyxl 已装） | 无 | 必需（覆盖深交所 5+ 接口，含融资融券官方容灾源） |
| `to_df()` 包装函数 | 新增辅助 | 零 | 无 | 必需（双契约，调用方按需转 DataFrame） |
| `@source` 加 `sign`/`parse` 字段 | 契约扩展 | 零 | 向后兼容 | 可选（元数据更完整） |
| sgw 新增 `legu`/`exchange` 组 | config 扩展 | 零 | 无 | 可选（按源决策） |

### 5.3 返回类型契约（评审已定：双契约）

asgk 现有 50 个函数全部返回 `list[dict]`。评审决定保持这一契约，同时提供 `to_df()` 包装：

```python
# asgk/_dataframe.py（新增）
"""DataFrame 包装：把 list[dict] 业务结果转 DataFrame，调用方按需用。"""
import pandas as pd

def to_df(data: list[dict]) -> pd.DataFrame:
    """list[dict] → DataFrame。"""
    return pd.DataFrame(data)

# 使用示例
from asgk import margin_trading, to_df
df = to_df(margin_trading("600519"))  # 调用方需要 DataFrame 时
```

**为什么是双契约而不是直接返 DataFrame**：
- 现有 50 函数契约不变（向后兼容）
- `list[dict]` 对所有调用方友好（含不用 pandas 的）
- pandas 跨版本 API 变动大，asgk 作为基础设施应稳定
- 需要分析能力时调用方一行 `to_df()` 即可

---

## 6. 终极可行性结论

### 6.1 方案 C 可行吗？

**完全可行。** 核查 akshare 全部 A 股模块：

- **P0（11 接口）**：100% 纯 JSON，零难点，与 asgk 现有范式同构
- **P1 难点（10+ 接口）**：100% 可移植，需 3 个工具模块前置（vendor JS + etree + xlsx 流）
- **真正需权衡**：仅申万 .xls（1 个，建议改东财源）+ curl_cffi JA3（P2 暂缓，有替代源）

### 6.2 asgk 设计需要升级吗？

**需要，代价极小**：
- 新增 `_signing.py`（vendor JS + mini-racer，~40 行）
- 新增 `_htmltable.py`（lxml.etree，~15 行）
- 新增 `_xlsx.py`（深交所 ShowReport 流解析，~10 行）
- 新增 `_dataframe.py`（`to_df()` 包装，~5 行）
- 可选：`@source` 扩展 `sign`/`parse` 元数据字段（向后兼容）
- 可选：sgw 新增乐咕/交易所限流组（config 级）

**修正后的关键设计原则**（替换错误的"零重依赖"）：
- ✅ 风控源经 sgw 网关（§2 合规）— 这是真正不可妥协的
- ✅ 实现层自由用 pandas/lxml/mini-racer（已装，零新增成本）
- ✅ 契约层返回 `list[dict]` + `to_df()` 包装（双契约）
- ✅ `@source` + tier/via 契约（向后兼容）
- 🔴 拒绝 curl_cffi（反反爬对抗走网关层，不进客户端）

### 6.3 关键证据：mini-racer 子场景 (c) 不存在

akshare 全部 A 股模块里，**没有"JS 解密加密响应"的场景**（子场景 c）。所有 mini-racer 用法都是签名（a）或算法（b）。
评审决定直接 vendor akshare 的 JS 用 mini-racer 执行（已装），无需纯 Python 重写——这是方案 C 工程量进一步降低的关键。

### 6.4 对合成方案执行 plan 的修订建议

[akshare-merge-design.md](akshare-merge-design.md) 的阶段拆解需补两处前置：

- **新增阶段 0.5**：实现 `_signing.py` + `_htmltable.py` + `_xlsx.py` + `_dataframe.py` 四个工具模块 + vendor JS 文件
- **新增阶段 0.6**：清理 asgk `pyproject.toml`——显式声明已用依赖（lxml 之前声明没用，现在要真用；如需 bs4 则加）
- **阶段 1 P0**：不变（纯 JSON，无需工具模块）
- **阶段 2 P1**：依赖阶段 0.5 的工具模块。**深交所融资融券官方源**（`margin_detail_szse`）原属"容灾候选"，现确认可移植，建议在本阶段移植以补齐东财被封兜底

---

## 7. 待评审决策点

> 评审已定三条（见 §3.1/§3.2/§5.3）：① vendor JS + mini-racer；② 双契约 list[dict] + to_df；③ 显式声明已用依赖。
> 以下尚未定：

1. **vendor JS 的同步策略**：akshare 上游 ths.js 变了怎么感知？（倾向：CI 定期 diff ref/akshare，变了告警人工更新）
2. **`@source` 是否扩展 `sign`/`parse` 字段**？（倾向是，元数据驱动文档生成）
3. **sgw 乐咕/交易所限流组**：进网关 vs 直连？（倾向：乐咕直连，交易所进网关）
4. **CYQ 筹码算法**：vendor akshare 的 JS 实现 vs 参考公开算法独立实现？（倾向后者，更易维护）
