# 筹码层（筹码分布 / 主力成本）

筹码分布与主力成本，本地 CYQ 算法计算（K 线经网关拉取，算法 vendor 自 akshare）。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `chip_distribution(symbol, adjust)` | 筹码分布+主力成本(单股近90日) | 东财push2his（百度日K降级）+本地CYQ | S |

## 调用示例

```bash
# 筹码分布（最近 90 日；纯数字代码，不带前缀）
asgk 信号 chip 000001
# 返回字段：date / benefit_part(获利比例,0-1) / avg_cost(元)
#          pct90_low~pct90_high / pct90_concentration
#          pct70_low~pct70_high / pct70_concentration

# 前复权筹码（--adjust q=前复权 / h=后复权 / 空=不复权）
asgk 信号 chip 600519 --adjust q
```

## 注意
- `symbol` 是**纯数字代码**（如 "000001"），不带市场前缀；市场由首字符 6 判定（6 开头=沪，否则=深）。
- `adjust` 复权：空=不复权 / `q`=前复权 / `h`=后复权。
- `benefit_part` 获利比例是**小数**（0.5 = 50%）。
- `avg_cost`/`pct90_low`/`pct90_high` 等成本单位是**元**。
- `pctXX_concentration` 集中度是小数（数值越小筹码越集中）。
- CYQ 算法是纯数学（换手率衰减+成交量加权三角分布），vendor 自 akshare 的 `cyq.js`（py_mini_racer 执行，无 DOM 依赖）。
- 拉取近 210 根 K 线计算，返回最近 90 日筹码分布；东财返回 `data=null` 或空
  K 线时自动使用百度日 K 继续计算。
- 日级盘后定稿（S档）。
