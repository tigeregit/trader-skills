# 研报层（个股研报 / 行业研报 / 一致预期EPS）

研报数据，东财研报经网关(P档)，同花顺EPS经网关(S档)。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `eastmoney_reports(code, max_pages)` | 个股研报+评级+三年EPS | 东财 | P |
| `eastmoney_industry_reports(industry_code, max_pages)` | 行业研报 | 东财 | P |
| `ths_eps_forecast(code)` | 机构一致预期EPS | 同花顺 | S |

## 调用示例

```bash
# 个股研报
asgk 研报 list 600519 --max-pages 1 --format json
# 返回 [{publishDate, orgSName, emRatingName, predictThisYearEps, infoCode, ...}]
# infoCode 用于下载研报 PDF 原文（见 docs 层）

# 行业研报（industry_code 用 * 拉一批，或从结果反查具体行业码）
asgk 研报 industry --industry-code '*' --max-pages 1

# 一致预期EPS（估值用）
asgk 研报 eps 600519 --format json
# [{'年度':2026, '预测机构数':46, '均值':68.75, ...}]
# "均值" = 机构一致预期EPS，机构数<3 要谨慎
```

## 研报 record 关键字段
title / publishDate / orgSName(机构) / infoCode(拼PDF) / predictThisYearEps / emRatingName(评级) / indvInduName(行业)

## 注意
- 研报是**事件定稿型**（P档30天缓存），发布即不变。
- 行业码无公开码表，用 `industry_code="*"` 拉一批从结果反查。
- 前置：CLI 需能连上 asgk-server（见 [gateway](gateway.md) 配置）。
