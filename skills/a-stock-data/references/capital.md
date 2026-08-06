# 资金面 / 筹码层（融资融券 / 大宗 / 股东户数 / 分红 / 资金流）

资金与筹码数据，全部东财 datacenter 经网关。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `margin_trading(code)` | 融资融券明细(日级) | 东财datacenter | S |
| `block_trade(code)` | 大宗交易 | 东财datacenter | S |
| `holder_num_change(code)` | 股东户数变化(季度) | 东财datacenter | L |
| `dividend_history(code)` | 分红送转历史 | 东财datacenter | P |
| `stock_fund_flow_120d(code)` | 个股资金流(120日) | 东财push2his | S |

## 调用示例

```bash
# 融资融券明细（rzye=融资余额，单位元）
asgk flow margin 600519 --page-size 5

# 股东户数变化（筹码集中度信号：户数持续减少 = 筹码集中 = 主力吸筹）
asgk flow holders_n 600519 --page-size 3

# 120日资金流（main_net=主力净流入，单位元）
asgk flow fundflow 600519
```

## 注意
- 资金流金额单位是**元**（非万元），注意换算。
- `holder_num_change` 的 reportName 已校正为 `RPT_F10_EH_HOLDERNUM`（ref 原值失效）。
- 融资融券/大宗是日级盘后定稿（S档），盘中查返回昨日数据。
