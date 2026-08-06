# ETF期权层（合约清单 / T型报价 / 希腊字母）

ETF期权数据，新浪源直连（GBK，必带Referer）。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `sina_option_codes(underlying, call)` | 合约清单 | 新浪(直连) | S |
| `sina_option_tquote(code)` | T型报价(买卖五档/持仓) | 新浪(直连) | R |
| `sina_option_greeks(code)` | 希腊字母+IV | 新浪(直连) | R |

## 调用示例

```bash
# 50ETF 近月认购合约清单（--underlying: 510050/510300/588000/510500；--call true=认购 false=认沽）
asgk 衍生 opt_codes --underlying 510050 --call true
# 返回 {月份: [合约代码列表]}，取近月列表中间档≈平值

# T型报价 + 希腊字母（contract=从上一步挑出的合约代码）
asgk 衍生 opt_quote <合约代码>
asgk 衍生 opt_greek <合约代码>
# opt_quote 字段：name / strike / last / open_interest
# opt_greek 字段：delta / iv(小数,0.17=17%)
```

## 注意
- 必带 `Referer: https://stock.finance.sina.com.cn/`（已在函数内置），否则403。
- 希腊字母解析跳过 raw[1:4] 三个空串（已处理），`iv` 是小数（0.17=17%）。
- T型报价/希腊字母由交易所预算，无需本地 BSM。
