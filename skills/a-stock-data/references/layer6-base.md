# 基础数据层（财务快照 / F10 / 个股信息 / 财报三表）

公司基础数据。mootdx/新浪直连，东财个股信息经网关。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `mootdx_finance(code)` | 季报快照(37字段) | 通达信TCP(直连) | L |
| `mootdx_f10(code, name)` | 公司资料(9大类文本) | 通达信TCP(直连) | P |
| `eastmoney_stock_info(code)` | 行业/股本/市值/上市日 | 东财push2(网关) | S |
| `sina_financial_report(code, report_type)` | 财报三表 | 新浪(直连) | L |

## 调用示例

```python
from asgk import eastmoney_stock_info, sina_financial_report, mootdx_finance

# 个股基本面
info = eastmoney_stock_info("600519")
print(f"{info['name']} 行业:{info['industry']} 总市值:{info['mcap']/1e8:.0f}亿")

# 财报三表（利润表/资产负债表/现金流量表）
lrb = sina_financial_report("600519", "lrb", num=4)  # "lrb"/"fzb"/"llb"
for item in lrb:
    print(f"{item['报告期']} 净利润:{item.get('净利润','')}")

# mootdx 财务快照
fin = mootdx_finance("600519")
print(f"总股本:{fin.get('zongguben')}")
```

## 注意
- mootdx 需国内网络（TCP 7709）。
- `sina_financial_report` 的 report_type：`lrb`=利润表，`fzb`=资产负债表，`llb`=现金流量表。
- F10 的 "股东研究" 类目含大量历史数据（16000+ chars），可截断节省 token。
