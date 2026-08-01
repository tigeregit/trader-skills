# 行情层（K线 / 五档盘口 / 逐笔 / PE-PB-市值）

实时行情数据，全部**直连不经网关**（腾讯/百度/mootdx 均不封 IP）。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `tencent_quote(codes)` | PE/PB/市值/换手/涨跌停 | 腾讯(GBK) | R |
| `baidu_kline_with_ma(code)` | 日K带MA5/10/20 | 百度 | R |
| `mootdx_bars(code, frequency, offset)` | K线(多周期,不复权) | 通达信TCP | R |
| `mootdx_quotes(codes)` | 五档盘口(46字段) | 通达信TCP | R |
| `mootdx_transaction(code, date)` | 逐笔成交 | 通达信TCP | R |

## 调用示例

```python
from asgk import tencent_quote, baidu_kline_with_ma

# PE/PB/市值（最常用）
q = tencent_quote(["600519"])
print(q["600519"])  # {name, price, pe_ttm, pb, mcap_yi(亿), turnover_pct, limit_up, ...}

# 带均线的日K（日K首选；mootdx_bars 空数据时也会降级到百度）
bk = baidu_kline_with_ma("600519")
print(bk["keys"])   # [timestamp, time, open, close, volume, high, low, ..., ma5avgprice, ...]
print(bk["rows"][-5:])  # 最近5根

# rows 是与 keys 对应的 CSV 字符串；先 zip 再按字段取值
latest = dict(zip(bk["keys"], bk["rows"][-1].split(",")))
print(latest["time"], latest["close"], latest["ma5avgprice"])
```

## 注意
- **mootdx_bars**：mootdx 0.11.7 返回空日 K 时自动降级到百度，并保持
  `{open, close, high, low, vol, amount, datetime}` 返回契约；分钟/周/月频率不做
  非等价降级。
- **tencent_quote 字段索引**：PE(TTM)=索引39，PB=索引46（非43，43是振幅%），总市值(亿)=44。
- mootdx 数据**不复权**，跨除权日需自行复权。
- mootdx 需国内网络（TCP 7709），海外超时。
