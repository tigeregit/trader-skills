# 板块层（概念/行业板块成份股）

板块→成份股反向查询（现有 `em_hot_concept` 是个股→概念，此处相反）。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `board_constituents(symbol, kind)` | 板块成份股(板块→个股) | 东财push2 | S |

## 调用示例

```python
from asgk import board_constituents

# 概念板块成份股（名称或代码均可）
cons = board_constituents("融资融券", kind="concept")  # 或 "BK0655"
print(f"融资融券板块{len(cons)}只成份股")
for c in cons[:3]:
    print(f"  {c['name']}({c['code']}) 价{c['price']} 涨{c['pct']}% 换手{c['turnover']}%")

# 行业板块成份股
ind = board_constituents("小金属", kind="industry")  # 或 "BK1027"
print(f"小金属行业{len(ind)}只")
```

## 注意
- `symbol` 接受**板块名称**（如"融资融券"）或**板块代码**（如"BK0655"）；名称会先查辅助表转代码。
- `kind` 区分概念（`concept`，fs=m:90 t:3）和行业（`industry`，fs=m:90 t:2）。
- push2 端点主用无编号 `push2.eastmoney.com`（编号子域 `29./79.` 作降级备选，[§7 决策7]）。
- 分页用 push2 的 `pn/pz`（非 datacenter 的 pageNumber），按 `data.total` 判断。
- `pct`/`amplitude`/`turnover` 单位是**百分点**（如涨跌幅 3.5 = 3.5%）。
- 成交额 `amount` 单位是**元**。
- 日级盘后定稿（S档），自动遍历全部分页。
