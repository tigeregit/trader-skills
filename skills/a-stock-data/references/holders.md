# 股东层（十大股东 / 十大流通股东 / 股东持股变化 / 股东协同）

股东维度数据。十大股东走 emweb F10（单股），股东变化/协同走 datacenter（全市场）。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `top10_holders(symbol, date)` | 十大股东明细(单股) | 东财emweb | L |
| `top10_free_holders(symbol, date)` | 十大流通股东明细(单股) | 东财emweb | L |
| `holder_change(date)` | 股东持股变化统计(全市场) | 东财datacenter | L |
| `holder_teamwork(holder_type)` | 股东协同(按股东类型) | 东财datacenter | L |

## 调用示例

```python
from asgk import top10_holders, top10_free_holders, holder_change, holder_teamwork

# 十大股东（单股，报告期）
top10 = top10_holders("sh600519", "20240930")
for h in top10[:3]:
    print(f"  {h['rank']}. {h['name']} 持{h['hold_num']}股 占{h['ratio']:.2f}% {h['change']}")

# 十大流通股东
free = top10_free_holders("sh600519", "20240930")
for h in free[:3]:
    print(f"  {h['name']}({h['holder_type']}) 占流通{h['ratio']:.2f}%")

# 股东持股变化（全市场，报告期，数据量大）
ch = holder_change("20240930")
big = sorted(ch, key=lambda x: x['holder_market_cap'] or 0, reverse=True)[:5]
for c in big:
    print(f"  {c['holder_name']}({c['holder_type']}) 持股市值{c['holder_market_cap']/1e8:.1f}亿 增{c['holdup_num']}减{c['holddown_num']}")

# 股东协同（按类型，如社保）
team = holder_teamwork("社保")
for t in team[:3]:
    print(f"  {t['holder_name']} ↔ {t['coop_holder_name']} 协同{t['coop_num']}次")
```

## 注意
- 十大股东的 `symbol` 需**带市场前缀**（`sh600519`/`sz000001`），内部转大写。
- `ratio`（占总股本/流通股比例）是**百分点**（54.07 = 54.07%），非小数。
- `holder_change(date)` 是**全市场报告期扫描**，数据量极大（数万页），单次调用耗时长，建议盘后批量。
- `holder_teamwork` 的 `holder_type` 取值：全部/个人/基金/QFII/社保/券商/信托。
- 持股市值 `holder_market_cap` 单位是**元**。
- 均为季度定稿数据（L档），非报告期返回空列表。
