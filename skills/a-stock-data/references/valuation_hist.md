# 估值历史层（全市场 PE / PB 历史）

全市场历史市盈率/市净率，乐咕源直连（[§7 决策10] 验证无风控）。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `market_pe_lg(market)` | 全市场市盈率历史 | 乐咕(直连) | L |
| `market_pb_lg(market)` | 全市场市净率历史 | 乐咕(直连) | L |

## 调用示例

```python
from asgk import market_pe_lg, market_pb_lg

# 上证 PE 历史
pe = market_pe_lg("上证")  # 1999 年至今
print(f"上证PE历史{len(pe)}条")
latest = pe[-1]
print(f"  {latest['date']} 指数{latest['close']} PE{latest['pe']}")
# 当前 PE 处于历史分位？
pe_vals = [p['pe'] for p in pe if p['pe']]
pct = sum(1 for v in pe_vals if v < latest['pe']) / len(pe_vals)
print(f"  当前 PE 历史分位: {pct:.0%}")

# 创业板 PB 历史
pb = market_pb_lg("创业板")
print(f"创业板PB{len(pb)}条，最新{pb[-1]['pb']}")
```

## 注意
- `market` 是**市场关键词**（非代码）：PE 支持 上证/深证/创业板；PB 支持 上证/深证/创业板/科创版。
- PE 的科创版走单独 URL，暂不支持（如需可后续补）。
- 乐咕源**直连**（不走网关），需 token + CSRF cookie + Referer（函数内置）。
- token = md5(当日日期)，与 akshare 的 JS 版 hash_code 输出一致（真机验证），无需 vendor JS。
- 历史数据较长（上证 PE 自 1999 年起，约 300+ 条），日级定稿（L档）。
- 个股 PE/PB 不在此层（akshare snapshot 无此接口，个股估值用现有 `valuation` 层的 `full_valuation`）。
