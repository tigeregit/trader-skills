# 研报层（个股研报 / 行业研报 / 一致预期EPS）

研报数据，东财研报经网关(P档)，同花顺EPS经网关(S档)。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `eastmoney_reports(code, max_pages)` | 个股研报+评级+三年EPS | 东财 | P |
| `eastmoney_industry_reports(industry_code, max_pages)` | 行业研报 | 东财 | P |
| `ths_eps_forecast(code)` | 机构一致预期EPS | 同花顺 | S |

## 调用示例

```python
from asgk import eastmoney_reports, ths_eps_forecast

# 个股研报
reports = eastmoney_reports("600519", max_pages=1)
for r in reports[:3]:
    print(f"{r['publishDate'][:10]} {r['orgSName']} {r['emRatingName']} EPS预测:{r.get('predictThisYearEps')}")

# 一致预期EPS（估值用）
eps = ths_eps_forecast("600519")
# [{'年度':2026, '预测机构数':46, '均值':68.75, ...}, ...]
# "均值" = 机构一致预期EPS，机构数<3 要谨慎
```

## 研报 record 关键字段
title / publishDate / orgSName(机构) / infoCode(拼PDF) / predictThisYearEps / emRatingName(评级) / indvInduName(行业)

## 注意
- 研报是**事件定稿型**（P档30天缓存），发布即不变。
- 行业码无公开码表，用 `industry_code="*"` 拉一批从结果反查。
- 前置：`export ASGK_GW=http://localhost:7700` 让请求走网关。
