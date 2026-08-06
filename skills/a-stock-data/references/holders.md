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

```bash
# 十大股东（单股，报告期；--date 是报告期）
asgk flow top10 600519 2024-09-30
# 十大流通股东
asgk flow top10_f 600519 2024-09-30

# 股东持股变化（全市场，报告期；数据量大，建议盘后批量）
asgk flow holder_c 2024-09-30

# 股东协同（--date 留空=最新报告期）
asgk flow teamwork
```

## 注意
- 十大股东的位置参数是**6 位股票代码**（`600519`/`000001`），服务端按首位判断市场并内部转大写。
- `ratio`（占总股本/流通股比例）是**百分点**（54.07 = 54.07%），非小数。
- `holder_change(date)` 是**全市场报告期扫描**，数据量极大（数万页），单次调用耗时长，建议盘后批量。
- `holder_teamwork` 的 `holder_type` 取值：全部/个人/基金/QFII/社保/券商/信托。
- 持股市值 `holder_market_cap` 单位是**元**。
- 均为季度定稿数据（L档），非报告期返回空列表。
