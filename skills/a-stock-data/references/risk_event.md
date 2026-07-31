# 事件层（高管增减持 / 股票回购 / 机构调研）

公司事件类数据，全部全市场扫描，东财 datacenter 经网关。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `mgmt_trade()` | 董监高持股变动明细(全市场) | 东财datacenter | S |
| `repurchase()` | 股票回购明细(全市场) | 东财datacenter | S |
| `institute_research(start_date)` | 机构调研明细(按开始日期) | 东财datacenter | S |

## 调用示例

```python
from asgk import mgmt_trade, repurchase, institute_research

# 高管增减持（全市场，无参数）
trades = mgmt_trade()
print(f"今日变动{len(trades)}条")
for t in trades[:3]:
    sign = "增持" if t["change_shares"] > 0 else "减持"
    print(f"  {t['name']} {t['person']}({t['position']}) {sign}{abs(t['change_shares'])}股 均价{t['avg_price']}")

# 股票回购（全市场）
reps = repurchase()
ongoing = [r for r in reps if r["progress"] == "实施中"]
print(f"实施中回购{len(ongoing)}家")
for r in ongoing[:3]:
    print(f"  {r['name']} 计划{r['plan_amt_lower']/1e8:.1f}~{r['plan_amt_upper']/1e8:.1f}亿 已回购{r['done_amt'] or 0}")

# 机构调研（指定日期之后）
research = institute_research("20241201")
for r in research[:3]:
    print(f"  {r['name']} {r['receive_date']} {r['receive_object']}({r['org_type']}) @ {r['receive_place']}")
```

## 注意
- `mgmt_trade`/`repurchase` 是**全市场无参扫描**，拉取全历史数据，量极大（高管增持股可达数十万行、数千页；回购全市场数千家）。单次调用耗时较长（经网关限流，每页 ≤1 req/s），内存占用大。建议按需调用（如盘后批量拉取缓存），不要高频实时调用。如需限制，可自行在调用层做时间窗口过滤。
- `institute_research(start_date)` 参数是**调研开始日期**（YYYYMMDD），返回该日期之后的所有调研，非股票代码。
- 回购 `progress` 已做易读映射：`董事会预案`/`股东大会通过`/`股东大会否决`/`实施中`/`停止实施`/`完成实施`（对齐东财代码 001-006）。
- 变动股数 `change_shares` 正数=增持，负数=减持。
- 金额单位均为**元**。
- 这些是日级盘后定稿数据（S档），盘中查返回截至昨日数据。
