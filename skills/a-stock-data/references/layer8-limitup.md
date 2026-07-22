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

```python
from asgk import em_zt_pool, limit_up_sentiment

# 涨停池
zt = em_zt_pool("20260722")  # date=YYYYMMDD
print(f"涨停{len(zt)}只")
for s in zt[:3]:
    print(f"  {s['name']} {s['zt_stat']} 封板{s['seal_fund']/1e8:.2f}亿 {s['industry']}")

# 打板情绪温度计
s = limit_up_sentiment("20260722")
print(f"涨停{s['zt_count']} 炸板率{s['break_rate']}% 最高{s['max_height']}连板")
print(f"连板梯队: {s['ladder']}")  # {板数: 家数}
```

## 注意
- `date` 必须传**交易日**（YYYYMMDD），非交易日 data 返回 null。
- 价格字段已 ÷1000（原始是×1000整数）。金额单位均为**元**。
- 四池盘中实时变化（R），盘后定稿（S）。
