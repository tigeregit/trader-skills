# TODO: P2 references 分层拆分

来源：docs/design.md 轴 1 / 落地路线 P2

## 背景

上游是单一 127KB / 2815 行的 SKILL.md，每次触发全量入上下文。按 progressive disclosure 原则，需把领域细节按数据层拆到 `references/`，model 按需读取单层，单次 token 降 80～90%。

## 待办

把上游 `ref/a-stock-data/SKILL.md` 的 10 层 + 估值 + 备用源，拆成 `skills/a-stock-data/references/` 下独立文件：

- [ ] `layer1-quote.md`（行情：mootdx / 腾讯 / 百度）
- [ ] `layer2-report.md`（研报：东财 / 同花顺 / iwencai）
- [ ] `layer3-signal.md`（信号：热点 / 北向 / 龙虎榜 / 解禁 / 行业）
- [ ] `layer4-capital.md`（资金面：融资融券 / 大宗 / 股东户数 / 分红 / 资金流）
- [ ] `layer5-news.md`（新闻：东财个股 / 财联社 / 全球资讯）
- [ ] `layer6-base.md`（基础数据：mootdx 财务/F10 / 东财信息 / 新浪三表）
- [ ] `layer7-announce.md`（公告：巨潮 / mootdx）
- [ ] `layer8-limitup.md`（打板：涨停/炸板/跌停池 + 题材情绪）
- [ ] `layer9-option.md`（ETF期权：T型报价 / 希腊字母 / IV）
- [ ] `layer10-sentiment.md`（舆情：互动易 / 热榜 / 人气榜）
- [ ] `valuation.md`（估值公式：前向PE / PE消化 / PEG / full_valuation）
- [ ] `failover.md`（备用源速查 & 降级策略）

## 验收标准

- 每个 reference 文件 = 该层脚本（`asgk`）的**使用说明 + 调用示例**，不再内嵌长实现。
- 实现部分替换为 `from asgk import ...` 调用（依赖 `scripts-library-port.md`）。
- 单个 reference 控制在约 150～250 行；SKILL.md 路由表能正确指向各文件。

## 依赖

- 依赖 `scripts-library-port.md`（reference 示例调用的是 `asgk` 库）。
