# 新闻层（个股新闻 / 财联社电报 / 全球资讯）

新闻快讯，流式数据(N档不缓存)。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `eastmoney_stock_news(code)` | 个股新闻 | 东财(经网关) | N |
| `cls_telegraph(page_size)` | 财联社电报(全市场) | cls.cn(直连,本地签名) | N |
| `eastmoney_global_news(page_size)` | 全球资讯(7×24) | 东财(经网关) | N |

## 调用示例

```python
from asgk import eastmoney_stock_news, cls_telegraph

# 个股新闻
news = eastmoney_stock_news("600519", page_size=5)
for n in news[:3]:
    print(f"{n['time']} {n['source']} {n['title'][:40]}")

# 财联社电报（与全球资讯互为备份）
tele = cls_telegraph(page_size=10)
print(tele[0]["title"][:50])
```

## 注意
- 新闻是**流式数据**(N档)，每次都是新内容，不缓存。
- `cls_telegraph` 直连 cls.cn + 本地签名（零key），不走网关。
- `eastmoney_global_news` 的 `sortEnd` 参数必须传日期（已修复，默认当天）。
- 个股新闻部分住宅IP间歇返回空（东财风控），空时换网络重试。
