# 信号层（热点 / 北向 / 板块 / 资金流 / 龙虎榜 / 解禁 / 行业）

市场信号数据。东财端点经网关，北向(hexin.cn)直连。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `ths_hot_reason(date)` | 当日强势股+题材归因 | 同花顺 | S |
| `hsgt_realtime()` | 北向分钟流向 | hexin.cn(直连) | R |
| `eastmoney_concept_blocks(code)` | 个股板块归属 | 东财push2 | S |
| `eastmoney_fund_flow_minute(code)` | 分钟资金流 | 东财push2 | R |
| `dragon_tiger_board(code, trade_date)` | 个股龙虎榜+席位 | 东财datacenter | S |
| `lockup_expiry(code, trade_date)` | 解禁日历 | 东财datacenter | S |
| `industry_comparison(top_n)` | 行业涨跌排名 | 东财push2 | R |
| `daily_dragon_tiger(trade_date)` | 全市场龙虎榜 | 东财datacenter | S |

## 调用示例

```bash
# 板块归属（题材归因）
asgk 信号 block 600519 --format json
# 返回 {concept_tags: ['食品饮料','白酒Ⅲ','贵州板块',...]}

# 全市场龙虎榜（盘后定稿）
asgk 信号 dragon_d 2026-07-22 --format json
# 返回 {total_records, ...}

# 个股龙虎榜（回看天数）
asgk 信号 dragon 600519 2026-07-22 --look-back 30

# 行业排名（前N）
asgk 信号 industry --top-n 5 --format json
# 返回 {top: [{name, ...}]}，涨幅前5行业
```

## 注意
- 龙虎榜/解禁是**盘后定稿**（S档），盘前查返回空属正常。
- 北向 `hsgt_realtime`：沪股通(hgt)可靠，深股通(sgt)自2024-08披露收紧仅参考。
- dragon_tiger_board 的 look_back 默认回看30天。
