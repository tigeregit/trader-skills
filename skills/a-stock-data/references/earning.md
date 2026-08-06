# 业绩层（业绩预告 / 业绩快报）

业绩预告与快报，按报告期全市场扫描（非单股查询），东财 datacenter 经网关。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `earning_forecast(date)` | 业绩预告(全市场报告期) | 东财datacenter | L |
| `earning_express(date)` | 业绩快报(全市场报告期) | 东财datacenter | L |

## 调用示例

```bash
# 业绩预告（某报告期全市场；参数是报告期 YYYY-MM-DD，非股票代码）
asgk base forecast 2024-09-30
# 返回字段：name / predict_finance / predict_type
#          predict_lower~predict_upper(元) / add_amp_lower~add_amp_upper(%)

# 业绩快报（某报告期全市场；返回确定数值 EPS/营收/净利）
asgk base express 2024-09-30
```

## 注意
- 参数 `date` 是**报告期**（YYYYMMDD，如 20240930），非股票代码、非公告日。
- 常见报告期：季报 `0331`/`0630`/`0930`/`1231`，从 20081231 开始有数据。
- 非报告期或未到披露季时返回空列表（正常，非报错）。
- 全市场扫描，返回量较大（大报告期可达数千家），自动遍历全部分页。
- 业绩预告给出的是**预测区间**（`predict_lower`~`predict_upper` + 变动幅度区间），业绩快报给出的是**确定数值**（EPS/营收/净利）。
- 金额单位均为**元**。
