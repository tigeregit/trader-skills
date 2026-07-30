# 风险/筛选层（股权质押 / 商誉）

风险排查与选股筛选数据，按日期/报告期全市场扫描，东财 datacenter 经网关。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `pledge_ratio(date)` | 股权质押比例(全市场按交易日) | 东财datacenter | S |
| `goodwill(date)` | 商誉明细(全市场按报告期) | 东财datacenter | L |

## 调用示例

```python
from asgk import pledge_ratio, goodwill

# 股权质押（某交易日全市场）
pr = pledge_ratio("20240906")  # date=YYYYMMDD 交易日
high = [p for p in pr if (p["pledge_ratio"] or 0) > 0.5]
print(f"质押比例>50%的高风险股:{len(high)}家")
for p in high[:3]:
    print(f"  {p['name']} 质押率{p['pledge_ratio']*100:.1f}% 质押{p['pledge_deal_num']}笔")

# 商誉（某报告期全市场，按商誉金额降序）
gw = goodwill("20231231")  # date=YYYYMMDD 报告期
print(f"有商誉公司:{len(gw)}家")
for g in gw[:3]:
    print(f"  {g['name']} 商誉{g['goodwill']/1e8:.2f}亿 占净资产{g['goodwill_to_equity']*100:.1f}% 净利{g['net_profit']/1e8:.2f}亿")
```

## 注意
- 参数 `date` 是**交易日**（质押）或**报告期**（商誉），YYYYMMDD 格式，非股票代码。
- `pledge_ratio` 是小数（0.02 = 2%），非百分比。
- 质押市值 `pledge_market_cap` 单位是**亿元**；商誉/净利润单位是**元**（注意换算）。
- 商誉接口需固定 token（已内置，非动态签名），报告期常见为季报末 `0331`/`0630`/`0930`/`1231`。
- 非交易日/非报告期返回空列表（正常，非报错）。
- 质押是日级盘后定稿（S档），商誉是季度定稿（L档）。
