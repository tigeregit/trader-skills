# TODO: P1 scripts 共享库移植

来源：notes/design.md 轴 3 / 落地路线 P1

> **移植规范见 `notes/asgk-contract.md`**（两层函数模型、@source 装饰器、签名规范、43 端点完整映射表）。每个端点移植时按契约第五节表的 tier/via/cli 标注，并遵循第七节移植流程。

## 背景

上游 `ref/a-stock-data/SKILL.md` 把全部实现**内嵌在 markdown 代码块**里，agent 每次触发 skill 都要重新拼装脚本。本项目场景下 100～1000 agent 重复拼装同一套代码是纯浪费（token + 重复请求）。需沉淀为共享库，agent 只 import 调用。

## 待办

按层移植上游函数到 `skills/a-stock-data/scripts/asgk/`：

- [ ] `client.py`：`tdx_client()` 封装 + mootdx BESTIP 空串 bug 规避（上游有实测备选服务器列表）。
- [ ] `em_proxy.py`：`em_get()` 改造为走网关（`gateway-mvp.md` 的产物），接口对齐上游。
- [ ] `quote.py`：`tencent_quote` / `baidu_kline_with_ma`。
- [ ] `reports.py`：`eastmoney_reports` / `eastmoney_industry_reports` / `ths_eps_forecast` / `iwencai_search`。
- [ ] `signal.py`：热点 / 北向 / 龙虎榜 / 解禁 / 行业排名。
- [ ] `capital.py`：融资融券 / 大宗交易 / 股东户数 / 分红 / 资金流。
- [ ] `news.py`：东财个股新闻 / 财联社电报 / 东财全球资讯。
- [ ] `base.py`：mootdx 财务/F10 / 东财个股信息 / 新浪财报三表。
- [ ] `announce.py`：巨潮公告检索 + PDF 下载（含 `_cninfo_orgid` 动态映射）。
- [ ] `limitup.py`：涨停/炸板/跌停/昨涨停四池 + 情绪。
- [ ] `option.py`：ETF 期权合约 / T型报价 / 希腊字母 + IV。
- [ ] `sentiment.py`：互动易 / 热榜 / 人气榜。
- [ ] `valuation.py`：`forward_pe` / `pe_digestion` / `calc_peg` / `full_valuation`。

## 验收标准

- 每个 `asgk` 模块接口与上游对应函数签名一致（除 `em_get` 走网关）。
- 移植时同步上游的失效接口修复与字段映射（见上游 CHANGELOG / SKILL.md 各版本说明）。
- 每个函数有最小 smoke test（用茅台 600519 等公开零 key 接口验证返回非空）。

## 依赖

- 依赖 `gateway-mvp.md`（东财系函数需走网关）。
- 不通联调、独立编译的层（如 `quote.py` 走腾讯/百度直连）可先行。
