# 能力代理架构重构设计

本文件是 asgk + sgw 架构重构的权威设计。将当前的「透明 HTTP 代理」重构为
「能力代理」：服务端拥有全部上游知识，客户端只发语义请求。

> **状态**：设计阶段（2026-08-06 起草）。本文档取代 `design.md` §三/§四、
> `gateway-design.md` 的透明代理部分、`asgk-contract.md` 的两层函数模型中
> 「底层 em_get 输入 URL」的前提。`data-source-risk-control.md` 的风控结论
> （限流阈值依据）延续不变。

## 一、问题诊断：为什么重构

### 1.1 现状：透明 HTTP 代理

```
agent → asgk 业务函数(硬编码全部上游知识) → em_get(url,params) → sgw(?u=url 透传) → 上游API
```

`em_get` 的输入是**原始 URL**（`asgk-contract.md:11`），sgw 收到 `?u=<URL>` 后
**原样转发**，不理解数据语义。sgw 是流量转发器，不是数据服务。

### 1.2 skill 侧硬编码了多少上游知识

调研（45 个 `@source` 业务函数逐个提取）：

| 上游知识类型 | 命中函数数 | 占比 |
|------------|----------|------|
| 硬编码 URL | 45/45 | 100% |
| 特殊 Header（Referer/Origin/UA） | 26 | 58% |
| JSON 路径解析 | ~35 | 78% |
| 东财 secid/f-字段号映射 | ~12 | 27% |
| CSV/字段索引数组解析 | 9 | 20% |
| GBK/字节解码 | 4 | 9% |
| POST form/json body | 6 | 13% |
| 非 HTTP / TCP 协议（mootdx） | 7 | 16% |
| 签名算法（md5/sha1） | 2 | 4% |
| CSRF 两步流 + 会话 cookie | 1 | 2% |
| 本地 vendor JS 执行（cyq.js） | 1 | 2% |

**100% 的上游 URL、签名、编码、字段映射硬编码在 skill 侧**。

### 1.3 三大症状（架构缺陷的具象表现）

1. **mootdx TCP 无法过 HTTP 代理**（7 函数直连）：通达信走 TCP 二进制协议
   （7709），sgw 只支持 HTTP。这 7 个函数（bars/quotes/transaction/f10/finance
   + market_pe/pb_lg 旁的 legulegu）绕过网关直连，有封 IP 风险且无熔断保护。
2. **legulegu CSRF 需跨请求会话**（已回退）：鉴权是两步（取 session cookie +
   带 cookie 调 API），无状态代理无法保证两次请求共享 session，被迫回退直连。
3. **新源/上游变更要改 skill + 网关两侧**：加一个源要改 asgk 函数（URL/解析）
   + sgw config（group/endpoint）+ inventory 测试，三处同步；上游改 URL 或加
   风控，要重新分发 asgk 到所有 100~1000 agent。

### 1.4 根因

`em_get(url, ...)` 的输入是 URL。网关只能做流量层的事（限流/缓存/熔断），
**不懂数据语义**。所有"这个数据怎么取"的知识被迫留在 skill 侧。这是从 ref
改造时「最小改动套限流」的路径依赖（`design.md` P0 阶段），不是有意的设计。

## 二、新架构：能力代理（capability proxy）

### 2.1 目标架构

```
┌──────────────────────────────────────────────────────────────┐
│ agent (100~1000个)                                            │
│   │                                                           │
│   ▼  调语义函数（code/date，无 URL/协议概念）                  │
│ asgk 客户端层（薄）                                            │
│   ├─ Python: tencent_quote(['600519']) → {price, pe, ...}     │
│   └─ CLI:    asgk quote 600519                                │
│   │  只描述「要什么数据」，不知道怎么取                          │
│   ▼  本机 RPC（HTTP JSON，localhost）                          │
│ ┌──────────────────────────────────────────────────────┐     │
│ │ asgk-server（能力服务端，吞噬 sgw）                    │     │
│ │  ├─ 语义路由: quote/kline/f10/announce/report/...     │     │
│ │  ├─ 上游知识: 全部 URL/协议/编码/鉴权/字段映射 在这     │     │
│ │  ├─ mootdx TCP 客户端池（在这，不在 skill）            │     │
│ │  ├─ curl_cffi 指纹 / cls 签名 / legulegu CSRF 会话     │     │
│ │  ├─ 流量内核（复用 sgw）: 限流+缓存+熔断+singleflight   │     │
│ │  ├─ 多源容灾: 主源失败自动降级到备源                    │     │
│ │  └─ 伪装成 single user 出网                            │     │
│ └──────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────┘
          │
          ▼
   东财/同花顺/腾讯/百度/新浪/巨潮/通达信TCP/…（上游多源）
```

### 2.2 核心原则

- **客户端默认只关心数据**：发语义请求（「我要 600519 的实时行情」）拿结构化
  数据，默认不关心具体来自哪个源。URL/HTTP/TCP/GBK/指纹等取数细节对客户端不可见。
- **源可查询、可指定**：当一个能力有多个数据源时，客户端可以：
  - `list_sources("quote")` → 列出该能力支持的所有源（如 `["tencent","sina","eastmoney"]`）
  - `tencent_quote(['600519'], source="sina")` → 显式指定走某源（用于对比/调试/绕过熔断）
  - 不指定时由服务端按优先级 + 健康度自动选源（含熔断降级）
  - 单源能力（如公告只有巨潮）则 `list_sources` 返回单元素列表，`source` 传错报错
- **服务端是数据能力代理**：拥有所有上游知识，负责选源、取数、解析、限流、
  缓存、容灾。对外接口是**数据能力**（quote/kline/f10...），不是 URL 转发。
- **协议无关**：服务端内部知道怎么用 TCP 取通达信、用 curl_cffi 取百度、
  用签名取财联——对客户端统一暴露语义接口，客户端无感。

### 2.3 与现状的对照

| 维度 | 现状（透明代理） | 新架构（能力代理） |
|------|----------------|------------------|
| skill 知道什么 | URL、协议、编码、鉴权、全部上游细节 | 只知道「要什么数据」 |
| 服务端角色 | 流量转发器（限流+缓存） | 数据服务（选源+取数+解析+限流+容灾） |
| mootdx | TCP 无法 HTTP 代理，被迫直连 | 服务端内嵌 TCP 客户端，天然解决 |
| legulegu CSRF | 无状态代理无法保持会话 | 服务端持有会话，天然解决 |
| 加新源 | 改 skill（函数+URL+解析）+ 网关 config + 测试 | 服务端加适配器，客户端接口不变 |
| 多源对比/切换 | 不支持（源 hardcode 在函数名里） | `source` 参数指定 + `list_sources` 发现 |
| 上游变更 | 改 skill + 重分发所有 agent | 只改服务端一处 |

## 三、契约设计

### 3.1 服务端语义接口

按数据域暴露能力，输入语义参数，输出结构化数据。接口示例：

```
POST /v1/quote        {"codes": ["600519"]}                        → {code: {price, pe_ttm, pb, ...}}
POST /v1/quote        {"codes": ["600519"], "source": "sina"}      → 显式指定源（多源能力）
POST /v1/kline        {"code": "600519", "ma": true}               → {keys, rows}
POST /v1/kline        {"code": "600519", "source": "mootdx"}       → 强制走 mootdx（不降级百度）
POST /v1/f10          {"code": "600519", "name": "公司概况"}        → "文本"
POST /v1/announce     {"code": "600519", "page_size": 30}          → [{title, date, url}]
POST /v1/report       {"code": "600519"}                           → [{rating, eps, ...}]
...
GET  /v1/sources      ?capability=quote                            → ["tencent","sina","eastmoney"]
```

约定：
- **`source` 可选参数**：多源能力（如 quote/kline/realtime）支持显式指定源。
  不传时服务端按优先级 + 健康度（熔断状态）自动选源；主源熔断自动降级备源。
  单源能力（如 announce 只有 cninfo）传 `source` 需匹配唯一源，否则报错。
- **`GET /v1/sources`**：列出某能力支持的所有源，供客户端发现/校验。不带
  `capability` 时返回全部能力及其源映射。
- 参数语义化（code/date/page_size），不含 URL/协议/header。返回值结构与现有
  业务函数一致（dict/list），保证客户端零改动。

### 3.2 能力注册表（服务端持有）

每个能力声明元数据，驱动选源/限流/缓存/容灾：

```python
@capability(
    name="quote",
    domain="行情",
    sources=[                           # 多源，按优先级；可被客户端 list/指定
        {"name": "tencent", "tier": "R", "group": "tencent", "healthy": True},
        {"name": "sina",    "tier": "R", "group": "sina",    "healthy": True},
    ],
    default_source="tencent",           # 不指定 source 时首选（熔断则降级下一优先级）
    fallback=None,                       # 自动降级链（如 kline: mootdx空→baidu）
    data_type="kv",                     # 数据形态(kv/table/series/text/doc) 驱动客户端格式校验
    supported_formats=["json", "md"],   # 该能力支持的输出格式（客户端格式化用）
)
def fetch_quote(codes: list[str], source: str | None = None) -> dict:
    # 服务端实现：选源(显式或自动)→构造请求→取数→解析→返回结构化数据
    ...
```

约定：
- `sources` 列出该能力的**全部可用源**（驱动 `GET /v1/sources`），按优先级排序；
  每个 source 的 `healthy` 由熔断器实时更新，自动选源时跳过不健康的。
- `default_source`：客户端不传 `source` 时的首选；主源熔断自动降级到下一健康源。
- 客户端传 `source` 时绕过自动选源，强制走指定源（若该源熔断则报错而非降级——
  显式指定意味着客户端明确要这个源的数据）。

这是现有 `@source(tier, via, cli)` 装饰器的演进：`via`（direct/gateway）被
`sources`（可枚举可指定的多源 + 限流组 + 健康度）取代；URL/编码/签名等实现细节
不再是元数据，而是能力函数体内的实现（对客户端不可见）。

### 3.3 客户端双入口

**Python（零破坏）**：保留 `tencent_quote(['600519'])` 等全部函数名与签名，
内部从「拼 URL + em_get」改为「调服务端语义接口」。agent 和 references 文档
零改动（`source` 是新增的可选参数，不传时行为不变）。

```python
# 重构后内部（agent 无感）
def tencent_quote(codes, source=None):
    return _server_call("quote", {"codes": codes, "source": source})  # 替代 em_get(qt.gtimg.cn...)

# 多源能力支持显式指定源（可选，新增能力）
tencent_quote(['600519'], source="sina")  # 强制走新浪
```

> 注：函数名 `tencent_quote` 保留是为了零破坏（历史调用方仍在用），语义上
> 它现在代表「实时行情」能力而非「腾讯这个源」。新代码建议用更显式的别名
> `quote = tencent_quote`（在 `__init__.py` 导出）。源选择通过 `source` 参数，
> 不通过函数名。

**CLI（新增，兑现承诺）**：`asgk-contract.md` 第六节承诺但未实现的 CLI：

```
asgk quote 600519                  # 等价 tencent_quote(['600519'])
asgk quote 600519 --source sina    # 显式指定源
asgk quote --sources               # 列出 quote 能力支持的源
asgk kline 600519                  # 等价 baidu_kline_with_ma('600519')
asgk report 600519                 # 等价 eastmoney_reports('600519')
asgk announce 600519               # 等价 cninfo_announcements('600519')
--format json|table                # 默认 table，json 给管道
```

CLI 直接调服务端，不经 Python 库（shell/其他语言也能用）。`--source` / `--sources`
对应语义接口的 `source` 参数和 `GET /v1/sources`。

### 3.4 em_get 的兼容角色（渐进迁移的枢纽）

**em_get 签名不变，内部实现切换**——这是兼容渐进的关键：

```
阶段1: em_get(url, params, tier) 内部 → sgw(?u=url)        # 现状
阶段N: em_get(url, params, tier) 内部 → server.semantic()  # 渐进切换后
最终:  em_get 废弃（所有业务函数直接调语义接口）
```

迁移期内，已下沉的能力走服务端语义接口，未下沉的仍走旧 em_get+sgw 路径。
**业务函数零改动**，em_get 内部按 URL 路由（已注册的语义能力走新路径，其余
走旧路径）。这让每阶段的迁移互相独立，主分支全程可用。

### 3.5 客户端格式化与交付（输出层）

**服务端只返结构化数据（dict/list），格式化与交付全在客户端。** 这保证：
服务端无状态、缓存不受格式影响（同数据只缓存一份，多格式按需渲染）、多个 agent
共享缓存不被格式碎片化。

#### 能力数据类型 → 支持格式矩阵

不同数据类型天然支持不同格式（由数据形态决定，非主观限制）：

| 数据类型 | 例子 | 支持格式 | 不支持 |
|---------|------|---------|--------|
| **表格型**（list[dict]） | 行情/研报/龙虎榜/公告 | `json` `csv` `md` `xlsx` | — |
| **键值型**（dict） | 单票估值/盘口 | `json` `md` | csv(单行无意义)、xlsx |
| **序列型**（K线/资金流） | kline/fund_flow | `json` `csv` `md` `xlsx` | — |
| **文本型**（F10/研报正文） | mootdx_f10 | `json` `md` `plain` | csv、xlsx |
| **文档型**（公告 PDF/年报） | 公告原文下载 | `pdf`(原文) `md`(摘要) | csv、json |

客户端请求不支持的组合时报错（如对 F10 请求 csv → `ValueError: 文本型不支持 csv`）。

#### 交付方式

- **`return`（默认）**：返回 Python 对象（dict/list/str/bytes）
- **`print`**：格式化后打印到 stdout（CLI 默认）
- **`file`**：写入文件，返回路径

#### 接口设计

Python（业务函数加 `format`/`output` 可选参数，不传时行为不变——零破坏）：

```python
# 默认: 返回结构化 dict（零破坏，现有代码无感）
tencent_quote(['600519'])                              → dict

# 指定格式 + 交付（新增可选参数）
tencent_quote(['600519'], format='csv')                → "code,price,pe\n600519,1309,19.7" (str)
tencent_quote(['600519'], format='md', output='print') → 打印 markdown 表格到 stdout
dragon_tiger_board('600519', format='xlsx', output='file', path='./dt.xlsx') → './dt.xlsx'
mootdx_f10('600519', format='md')                      → markdown 文本（文本型不支持csv）
```

CLI（`--format` / `--output` / `--path`）：

```
asgk quote 600519                          # 默认 table 打印
asgk quote 600519 --format json            # JSON 打印
asgk quote 600519 --format csv --output file --path quotes.csv
asgk kline 600519 --format xlsx --output file --path k.xlsx
asgk f10 600519 --format md                # 文本型 → markdown
```

#### 实现归属

- **格式化**：客户端库 `asgk/_format.py`（新增），按数据类型分发：
  - `csv`/`md`：纯 Python（csv 标准库 + 简单表格渲染）
  - `json`：标准库
  - `xlsx`：pandas + openpyxl（已是现有依赖，`_xlsx.py` 在用）
  - `plain`：原样文本
- **交付**：客户端库 `asgk/_output.py`（新增），return/print/file 三态
- **格式校验**：每个能力在注册表声明 `supported_formats`，客户端请求前校验，
  不支持的组合在客户端就报错（不打扰服务端）

```python
@capability(
    name="f10",
    data_type="text",           # 驱动格式校验
    supported_formats=["json", "md", "plain"],
    ...
)
```

> 设计要点：格式化是纯客户端计算，无网络无状态。它与 §3.4 的 em_get 兼容、
> §3.3 的双入口正交——format/output 参数在 Python 函数和 CLI 两侧一致暴露，
> 服务端完全不感知。纯计算函数（valuation 的 forward_pe 等）同样支持格式化。

### 3.6 cache 机制 refactor（核心）

当前 sgw 的 cache 是 **URL 级 + 存原始字节 + tier 一刀切 TTL**。新架构下这三
点都要变。这是流量内核搬入服务端时**改动最大**的部分（其余限流/熔断/singleflight
几乎零改）。

#### a. 存什么：解析后的结构化数据，非原始字节

```
现状(sgw):   cache 存 r.content（上游原始字节，GBK文本/JSON）
新架构:      cache 存 fetch_xxx() 的返回值（解析后的 dict/list）
```

理由：
- 能力代理后，服务端职责是"取数+解析"，cache 应在解析**之后**——命中即返结构化
  数据，客户端零解析开销（§3.5 格式化直接作用于结构化数据）。
- 原始字节 cache 的问题是：每次命中客户端还要重新解析（GBK 解码、字段映射），
  且不同 source 的原始字节格式不同（腾讯 GBK vs 新浪 GBK 字段顺序不同），无法
  归一化。

#### b. cache key：语义参数 + source，非 URL

```
现状:   "R|https://qt.gtimg.cn/q?q=sh600519"           # tier|canonical_url
新架构: "quote|tencent|codes=600519"                    # capability|source|语义参数
```

- key 第一段是 **capability 名**（quote/kline/f10...），不是 URL。
- 第二段是 **source**（tencent/sina/...）——**per-source 独立缓存，不跨源共享**。
  理由：不同源的数据值不等价（腾讯 vs 新浪实时价有秒级差；东财 vs 同花顺 PE 口径
  不同），跨源共享会脏读。
- 第三段是**语义参数的规范化哈希**（codes/date/page_size 等，排序后哈希）。

**per-source 缓存的命中逻辑**：
- 不指定 source → 用 default_source，命中 default_source 的 cache
- 指定 source → 查该 source 的 cache；无则取数（**不复用其他 source 的 cache**）；
  若该 source 熔断，报错而非降级取其他源（显式指定 = 客户端明确要这个源）
- 自动降级（不指定 source 时）→ 主源熔断，**降级源的 cache 独立于主源**，各自
  缓存，互不污染

#### c. 按数据类型差异化 cache 策略（不再 tier 一刀切）

当前 5 档（P/L/S/R/N）只控 TTL。新架构按**数据更新特性**分 6 类，每类有独立
的 TTL + 存储方式 + 缓存粒度：

| 数据类型 | 例子 | 更新特性 | TTL | 存储方式 | 落盘 | 粒度 |
|---------|------|---------|-----|---------|------|------|
| **定稿型** | 公告/分红/F10/互动易 | 发布即不改 | 30天 | 结构化 | 是 | per-code |
| **季度型** | 财报三表/股东户数/业绩预告 | 季度更新 | 1天 | 结构化 | 是 | per-code |
| **日级型(盘后定稿)** | 龙虎榜/融资融券/大宗/板块 | 盘中变、盘后定稿 | 盘中0/盘后12h | 结构化 | 否 | per-code |
| **日级型(随时变)** | 研报评级/质押/解禁 | 日内可能变 | 1h | 结构化 | 否 | per-code |
| **实时型** | 行情/K线/盘口/资金流/涨停池 | 秒级变 | 0(no-cache) | — | 否 | — |
| **流式型** | 新闻电报 | 持续追加 | 0(no-cache) | — | 否 | — |

**vs 现状的改进**：
1. **拆分 P 档**：原 P 档混了"公告(真定稿30天)"和"研报(评级会变)"。新分
   "定稿型(30天)"和"日级型随时变(1h)"——研报评级 1h TTL，避免拿到过时评级。
2. **拆分 S 档**：原 S 档都是"盘后定稿"。但质押/解禁/研报其实日内会更新，
   归到"日级型随时变(1h)"更准确；龙虎榜/融资融券才是真"盘后定稿"。
3. **实时型仍 no-cache**但保留 singleflight（同秒 1000 agent 取同一票合并为
   一次出网 + 一次解析，结果广播给所有 follower）。

> 能力注册表的 `cache_policy` 字段声明所属类型，驱动 TTL/存储/落盘：
> ```python
> @capability(name="announce", cache_policy="definitive", ...)   # 定稿型
> @capability(name="report",   cache_policy="daily_volatile", ...)  # 日级随时变
> @capability(name="quote",    cache_policy="realtime", ...)     # 实时型
> ```

#### d. 存储方式：内存 + 磁盘（沿用 sgw 双层，存的内容变了）

- **内存 Cache**（搬 sgw）：存结构化数据（dict/list，JSON 序列化进 dict），
  命中即返。原样复用 sgw 的 Cache 类（key/value/ttl/expire）。
- **磁盘 DiskCache**（搬 sgw）：仅"定稿型"+"季度型"落盘（P/L 对应），重启
  恢复。复用 sgw 的 DiskCache（SQLite+WAL），但存的 BLOB 从"上游字节"改为
  "结构化数据的 JSON"。原样复用 write-through + load_all 回填 + 惰性过期删除。
- **singleflight**（搬 sgw，零改）：所有类型（含 realtime 的 TTL=0）都走
  singleflight 合并并发 miss。realtime 型虽不 cache，但同秒并发合并为一次
  出网+解析，结果广播。这是 sgw 已有的设计（`proxy.py:1112` 注释"即使 TTL=0
  也合并"），原样保留。

#### e. 与客户端格式化层（§3.5）的关系

- cache 在**格式化之前**：服务端 cache 存结构化数据，格式化在客户端。
- 同一份数据（如 600519 的 quote）只 cache 一份结构化 dict，N 个 agent 各自
  按需格式化（csv/json/md）——**格式不进 cache key，不制造缓存碎片**。
- 这正是 §3.5 把格式化放客户端的核心收益：服务端 cache 命中率高，不被格式碎片化。

#### f. cache key 规范化（取代 _canonical_url）

现状用 `_canonical_url(url, params, header_key_parts, ignored_params)` 规范 URL。
新架构改为 `_semantic_key(capability, source, params)`：
- 输入是语义参数 dict（codes/date/page_size），非 URL
- 排序 + 规范化（codes 列表排序，去重）后哈希
- **不含** source（source 是 key 第二段，独立）
- **不含** format/output（格式化在客户端，不进服务端 cache key）
- ignored_params 概念保留（如东财的 ut 凭据参数不进 key 但仍发上游），
  但作用域从"query param"改为"语义参数"

## 四、sgw 复用方案（代码级）

### 4.1 直接搬用（零改，约 70% 代码量）

| 代码块 | proxy.py 位置 | 说明 |
|--------|--------------|------|
| `TokenBucket` | 707-740 | 限流，纯 acquire() 接口 |
| `Cache` | 744-770 | 内存缓存（**存的内容变：结构化数据非字节，见 §3.6**）|
| `DiskCache` | 774-860 | SQLite+WAL（**存的内容变 + key 规范化改，见 §3.6**）|
| `SingleFlight` | 166-189 | 并发 miss 合并（零改，含 realtime TTL=0 的合并）|
| `CircuitBreaker` | 192-294 | 熔断 + canary 探针 |
| `CircuitStateStore` | 297-396 | 熔断状态 SQLite 主库 |
| `CircuitStateManager` | 398-683 | **安全闩，最该复用，绝不该重写** |
| 五档 TTL + 盘中判断 + fallback | 981-1007 | 缓存分档（**拆为 6 类数据类型，见 §3.6c**）|
| retry/backoff 骨架 | 1181-1229 | 指数退避 |
| 指纹日志 | 1010-1042 | key 语义改，骨架搬 |

这些代码已过真实风控源考验（家庭 IP 安全闩、跨重启熔断持久化、P/L 落盘恢复），
重写代价高、风险大。搬入服务端作为「流量中间件层」。**cache 部分（Cache/DiskCache/
分档）是搬入时改动最大的**——存储内容从原始字节变结构化数据、key 从 URL 变语义
参数、分档从 5 档细化到 6 类数据类型，详见 §3.6。

### 4.2 改造搬用

| 代码块 | 改造点 |
|--------|--------|
| `EndpointPolicy`（101-157） | 六轴概念保留，`matches(host,path)` 的 fnmatch 改为能力注册表查找 |
| `_egress_request`（1143-1159） | 双客户端（requests/curl_cffi）保留，`egress_client` 由能力声明 |
| `_fetch_upstream`（1161-1233） | 核心循环保留（限流→熔断→出网→缓存），入参从 target_url 改为「请求构造」 |

### 4.3 丢弃（透明代理包袱）

- `make_handler` / `do_GET` / `do_POST`（1261-1356）—— 整个 HTTP 透明协议层
- `handle(target_url, ...)` 签名（1045-1141）
- `_canonical_url`（72-98）—— 被语义 cache key 取代
- `group_of` / `policy_for` 的 host/path 匹配（964-978）
- `_filtered_client_headers` + 头白名单透传（687-703, 1076-1095）
- `?u=` 转发协议

## 五、迁移路径（兼容渐进，分阶段）

每阶段：主分支可用（业务函数零改动）+ 独立测试 + 独立提交。

### 阶段 1：服务端骨架 + 流量内核 + 试点能力

- 新建 `packages/asgk-server/`，搬入 sgw 四大流量基础设施（§4.1）
- 实现服务端框架：能力注册表 + RPC 入口（HTTP JSON，localhost）
- 第一个能力 `quote`（验证闭环：客户端调 → 服务端取腾讯 → 返回结构化）
- em_get 内部对已注册能力走服务端，其余走旧路径
- **验收**：`tencent_quote(['600519'])` 经服务端返回真实数据，其余函数不受影响

### 阶段 2：零成本梯队（13 个 `_datacenter` 族）

`capital/earning/risk_event/pool_filter/holders/signal` 里用
`_datacenter(reportName, filter)` 的 13 个函数。上游知识 = reportName + filter
模板 + 字段映射。整体收进服务端的「东财 datacenter 端点注册表」。

### 阶段 3：低成本梯队（~18 个东财 push2 族）

URL + f-字段表 + ut 常量 + Referer。参数化为「东财 push2 端点描述」。

### 阶段 4：中成本梯队（~7 个编码/解析）

GBK 解码、CSV split、JSONP 剥壳、xlsx 解析、字段索引数组。

### 阶段 5：高成本梯队（5 个算法/协议硬骨头）

1. cls 签名（md5(sha1)）
2. legulegu CSRF 会话（服务端持有 cookie，解决回退问题）
3. 百度 curl_cffi 指纹（服务端内部，对客户端无感）
4. chip 的 cyq.js 执行（py_mini_racer 在服务端跑）
5. **mootdx TCP 客户端池**（服务端内嵌，解决 7 函数直连问题——新架构的核心收益）

### 阶段 6：废弃 sgw + CLI 落地

- sgw 包标记废弃（保留代码供回退，但不再部署）
- 实现 `asgk/cli.py`（兑现 asgk-contract.md 第六节承诺）
- 更新 SKILL.md / references 反映双入口

## 六、风险与约束

### 6.1 强契约重构
`test_endpoint_inventory.py` 静态扫描所有 `em_get` URL 与 sgw 端点策略双向对账，
CI 强制。重构改调用形态后，这个测试要从「URL 对账」改为「能力注册对账」，
随阶段 1 同步重构。

### 6.2 状态持久化兼容
`state/sgw_state.db` + `sgw_safety_latch.json` + `cache/sgw_cache.db` 已在
systemd 部署中固化（`sgw-service.sh`）。服务端搬入时保持路径，避免破坏现有
部署。systemd unit 从 sgw.service 演进为 asgk-server.service。

### 6.3 纯计算函数不下沉
`valuation.py` 的 `forward_pe` / `pe_digestion` / `calc_peg` 是纯本地计算，
无网络，留在客户端。`full_valuation`（串联多源）内部调语义接口。纯计算函数
同样支持 §3.5 的客户端格式化（format/output 参数），因其返回值也是结构化数据。

### 6.4 rps 取值不动
各限流组 rps（社区保守 ×1.5，见 config.toml 注释 + data-source-risk-control.md）
不在重构中改动，除非有新调研。流量内核搬入服务端时，限流配置（config.toml
的 group 段）原样保留。

### 6.5 渐进迁移的 em_get 路由
迁移期内 em_get 内部按 URL 路由（已注册能力走服务端，其余走旧 sgw 路径）。
需确保路由判定准确，避免「同一能力一半走新一半走旧」的不一致。建议用一个
显式的「已下沉能力清单」驱动路由，而非 URL 模式匹配。

## 七、与现有文档的关系

| 文档 | 处理 |
|------|------|
| 本文档 | **权威**，取代下列的对应部分 |
| `design.md` | §三（共享网关）、§四（asgk 共享库）被本文档 §二/§三 取代 |
| `gateway-design.md` | 透明代理设计（handle/?u=/_canonical_url）被本文档 §四 标注丢弃；限流/缓存/熔断设计延续 |
| `asgk-contract.md` | 「两层函数模型」的「底层 em_get 输入 URL」前提被本文档 §3.4 取代；签名规范/返回类型/CLI 契约延续 |
| `data-source-risk-control.md` | 风控结论（限流阈值依据）延续不变；迁移进度（已迁移/仍直连）随阶段更新 |

## 附录：调研数据来源

本文档基于 2026-08-06 的两份并行 Explore 调研：
- asgk 上游知识分布：45 个业务函数逐个提取，13 个已验证参数化可行，5 个算法/协议硬骨头
- sgw 能力清单：四大流量基础设施与透明代理耦合极浅，70% 代码可直搬，熔断安全闩最该复用
