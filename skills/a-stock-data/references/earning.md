# 业绩层（业绩预告 / 业绩快报）

业绩预告与快报，按报告期全市场扫描（非单股查询），东财 datacenter 经网关。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `earning_forecast(date)` | 业绩预告(全市场报告期) | 东财datacenter | L |
| `earning_express(date)` | 业绩快报(全市场报告期) | 东财datacenter | L |

## 调用示例

```python
from asgk import earning_forecast, earning_express

# 业绩预告（某报告期全市场，返回所有发布预告的公司）
fc = earning_forecast("20240930")  # date=YYYYMMDD 报告期
print(f"发布预告:{len(fc)}家")
for s in fc[:3]:
    print(f"  {s['name']} {s['predict_finance']} {s['predict_type']}")
    print(f"    {s['predict_lower']}~{s['predict_upper']}元 变动{s['add_amp_lower']}%~{s['add_amp_upper']}%")

# 业绩快报（某报告期全市场，已出快报的公司）
kb = earning_express("20240930")
for s in kb[:3]:
    print(f"  {s['name']} EPS{s['eps']} 净利{s['net_profit']/1e8:.2f}亿 同比{s['profit_yoy']}%")
```

## 注意
- 参数 `date` 是**报告期**（YYYYMMDD，如 20240930），非股票代码、非公告日。
- 常见报告期：季报 `0331`/`0630`/`0930`/`1231`，从 20081231 开始有数据。
- 非报告期或未到披露季时返回空列表（正常，非报错）。
- 全市场扫描，返回量较大（大报告期可达数千家），自动遍历全部分页。
- 业绩预告给出的是**预测区间**（`predict_lower`~`predict_upper` + 变动幅度区间），业绩快报给出的是**确定数值**（EPS/营收/净利）。
- 金额单位均为**元**。
