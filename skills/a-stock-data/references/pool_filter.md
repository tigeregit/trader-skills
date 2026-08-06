# 风险/筛选层（股权质押 / 商誉）

风险排查与选股筛选数据，按日期/报告期全市场扫描，东财 datacenter 经网关。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `pledge_ratio(date)` | 股权质押比例(全市场按交易日) | 东财datacenter | S |
| `goodwill(date)` | 商誉明细(全市场按报告期) | 东财datacenter | L |

## 调用示例

```bash
# 股权质押（参数=交易日 YYYY-MM-DD；pledge_ratio 单位是百分点）
asgk 风控 pledge 2024-09-06
# 字段：name / pledge_ratio(%,>50=高风险) / pledge_deal_num(笔数)
#      pledge_market_cap(质押市值,万元)

# 商誉（参数=报告期 YYYY-MM-DD；按商誉金额降序）
asgk 风控 goodwill 2023-12-31
# 字段：name / goodwill(元) / goodwill_to_equity(占净资产,小数) / net_profit(净利,元)
```

## 注意
- 参数 `date` 是**交易日**（质押）或**报告期**（商誉），YYYYMMDD 格式，非股票代码。
- `pledge_ratio` 是**百分点**（75.09 = 75.09%），非小数。
- `pledge_market_cap` 质押市值单位是**万元**；商誉/净利润单位是**元**（注意换算）。
- 商誉接口需固定 token（已内置，非动态签名），报告期常见为季报末 `0331`/`0630`/`0930`/`1231`。
- 非交易日/非报告期返回空列表（正常，非报错）。
- 质押是日级盘后定稿（S档），商誉是季度定稿（L档）。
