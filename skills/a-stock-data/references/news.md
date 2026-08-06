# 新闻层（个股新闻 / 财联社电报 / 全球资讯）

新闻快讯，流式数据(N档不缓存)。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `eastmoney_stock_news(code)` | 个股新闻 | 东财(经网关) | N |
| `cls_telegraph(page_size)` | 财联社电报(全市场) | cls.cn(直连,本地签名) | N |
| `eastmoney_global_news(page_size)` | 全球资讯(7×24) | 东财(经网关) | N |

## 调用示例

```bash
# 个股新闻
asgk 资讯 stock 600519 --page-size 5
# 返回字段：time / source / title

# 财联社电报（与全球资讯互为备份）
asgk 资讯 telegraph --page-size 10
```

## 注意
- 新闻是**流式数据**(N档)，每次都是新内容，不缓存。
- `cls_telegraph` 直连 cls.cn + 本地签名（零key），不走网关。
- `eastmoney_global_news` 的 `sortEnd` 参数必须传日期（已修复，默认当天）。
- 个股新闻部分住宅 IP 间歇只返回 `passportWeb` 或非 JSONP 风控页；函数将这类
  HTTP 200 响应归一为空列表。网关自身的 4xx/5xx 仍抛出请求错误，不伪装为空。
