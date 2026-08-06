# 打板层（涨停 / 炸板 / 跌停池 / 题材情绪）

打板与题材跟踪。东财四池经网关，盘中R/盘后S。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `em_zt_pool(date)` | 涨停池 | 东财push2ex(网关) | R |
| `em_zb_pool(date)` | 炸板池 | 东财push2ex(网关) | R |
| `em_dt_pool(date)` | 跌停池 | 东财push2ex(网关) | R |
| `em_yzt_pool(date)` | 昨涨停池(晋级率) | 东财push2ex(网关) | S |
| `ths_limit_up_pool(date)` | 涨停揭秘(原因/封板率) | 同花顺(网关) | R |
| `limit_up_sentiment(date)` | 打板情绪(炸板率/连板梯队) | 东财四池组合 | R |

## 调用示例

```bash
# 涨停池（参数=交易日 YYYY-MM-DD）
asgk 风控 zt 2026-07-22
# 返回字段：name / zt_stat / seal_fund(封板资金,元) / industry

# 打板情绪温度计
asgk 风控 sentiment 2026-07-22
# 返回字段：zt_count / break_rate(炸板率%) / max_height(最高连板) / ladder({板数: 家数})
```

## 注意
- `date` 必须传**交易日**（YYYYMMDD），非交易日 data 返回 null。
- 价格字段已 ÷1000（原始是×1000整数）。金额单位均为**元**。
- 四池盘中实时变化（R），盘后定稿（S）。
