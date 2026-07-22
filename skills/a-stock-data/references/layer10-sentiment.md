# 舆情互动层（互动易 / 热榜 / 人气榜 / 概念命中）

投资者互动 + 市场热度。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `cninfo_irm(code)` | 互动易问答(提问+回复) | 巨潮irm(直连POST) | P |
| `ths_hot_list(period)` | 同花顺热榜(人气+概念) | 同花顺(网关) | R |
| `em_hot_rank(top)` | 东财人气榜 | 东财emappdata(直连POST) | R |
| `em_hot_concept(code)` | 个股概念命中 | 东财emappdata(直连POST) | S |

## 调用示例

```python
from asgk import cninfo_irm, ths_hot_list, em_hot_concept

# 互动易（看公司怎么回应投资者）
irm = cninfo_irm("002594", page_size=5)
for q in irm:
    if q["answer"]:
        print(f"Q: {q['question'][:30]} A[{q['answerer']}]: {q['answer'][:50]}")

# 同花顺热榜
hot = ths_hot_list(period="hour")  # "hour"/"day"
print(f"TOP1: {hot[0]['name']} 热度{hot[0]['heat']}")

# 个股概念命中
hc = em_hot_concept("600519")
print([c["concept"] for c in hc[:3]])  # ['白酒', ...]
```

## 注意
- 互动易最新提问常未回复（answer=None），回复率因公司而异。
- `em_hot_rank`/`em_hot_concept` 是 POST+JSON（em_get 只支持GET，故直连）。
- 同花顺热榜 `period`：`hour`=小时榜，`day`=日榜。
