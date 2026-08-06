# 估值计算（前向PE / PE消化 / PEG / 单票估值全景）

纯本地计算（无网络），`full_valuation` 串联腾讯行情+一致预期EPS。

## 函数速查

| 函数 | 作用 | 网络 |
|------|------|------|
| `forward_pe(price, eps_forecast)` | 前向PE = 价/预期EPS | 无 |
| `pe_digestion(current_pe, cagr, target_pe)` | PE消化到目标需几年 | 无 |
| `calc_peg(pe, cagr)` | PEG = 前向PE/(CAGR×100) | 无 |
| `full_valuation(code)` | 单票估值全景(串联多源) | 腾讯行情+同花顺EPS |

## 调用示例

```bash
# 纯计算（不调服务端，本地直接算）
asgk 研报 fwd_pe 100 5            # 远期PE = 股价/EPS，返回 {forward_pe: 20.0}
asgk 研报 digest 60 0.3           # 60x消化到30x需几年，返回 {years: ...}
asgk 研报 digest 60 0.3 --target-pe 25
asgk 研报 peg 60 0.3              # PEG = PE/(CAGR*100)，返回 {peg: 2.0}

# 单票完整估值（一步到位，经服务端串联行情+EPS）
asgk 研报 valuation 600519 --format json
# 返回 {name, price, pe_ttm, pb, mcap_yi, eps_cur, eps_next,
#       pe_fwd, cagr_pct, peg, digest_years, analyst_count}
```

## 投资框架速查
```
壁垒 → 增速 → PE消化 → PEG校验
1. 有壁垒吗？ → 没有则排除
2. 增速多少？(CAGR > 30% 才有意义)
3. PE多久消化到30x？(< 2年合理, > 4年太贵)
4. PEG多少？(< 1 便宜, 1-1.5 合理, > 1.5 贵)
```

## 注意
- 30x PE 是A股成长股合理估值锚点（所有行业统一）。
- `full_valuation` 的 EPS 来自 `ths_eps_forecast`，机构覆盖<3 家时估值可靠性低。
- `full_valuation` 同时兼容 `ths_eps_forecast` 当前的 `list[dict]` 返回和历史
  DataFrame 返回。
- `pe_digestion` 的 cagr = 下一年EPS/当年EPS - 1。
