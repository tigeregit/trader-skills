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

```bash
# 互动易（看公司怎么回应投资者）
asgk event irm 002594 --page-size 5 --format json
# 返回 [{question, answer, answerer, ...}]，answer=None 表示未回复

# 同花顺热榜（period: hour/day）
asgk news hot_list --period hour
# 返回 [{name, heat, ...}]，第一条即 TOP1

# 个股概念命中
asgk news concept 600519 --format json
# 返回 [{concept, ...}]，如 ['白酒', ...]
```

## 注意
- 互动易最新提问常未回复（answer=None），回复率因公司而异。
- `em_hot_rank`/`em_hot_concept` 是 POST+JSON 请求（服务端 egress 处理）。
- 同花顺热榜 `period`：`hour`=小时榜，`day`=日榜。
