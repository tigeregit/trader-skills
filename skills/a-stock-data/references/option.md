# ETF期权层（合约清单 / T型报价 / 希腊字母）

ETF期权数据，新浪源直连（GBK，必带Referer）。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `sina_option_codes(underlying, call)` | 合约清单 | 新浪(直连) | S |
| `sina_option_tquote(code)` | T型报价(买卖五档/持仓) | 新浪(直连) | R |
| `sina_option_greeks(code)` | 希腊字母+IV | 新浪(直连) | R |

## 调用示例

```python
from asgk import sina_option_codes, sina_option_tquote, sina_option_greeks

# 50ETF 近月认购合约
codes = sina_option_codes("510050", call=True)  # underlying: 510050/510300/588000/510500
near_month = list(codes)[0]
contract = codes[near_month][len(codes[near_month]) // 2]  # 中间档≈平值

# T型报价 + 希腊字母
q = sina_option_tquote(contract)
g = sina_option_greeks(contract)
print(f"{q['name']} 行权价{q['strike']} 最新{q['last']} 持仓{q['open_interest']:.0f}")
print(f"  Delta={g['delta']} IV={g['iv']:.2%}")
```

## 注意
- 必带 `Referer: https://stock.finance.sina.com.cn/`（已在函数内置），否则403。
- 希腊字母解析跳过 raw[1:4] 三个空串（已处理），`iv` 是小数（0.17=17%）。
- T型报价/希腊字母由交易所预算，无需本地 BSM。
