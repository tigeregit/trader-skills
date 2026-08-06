# 公告层（巨潮公告检索）

上市公司公告，发布即定稿(P档)。

## 函数速查

| 函数 | 数据 | 源 | 档位 |
|------|------|----|----|
| `cninfo_announcements(code, page_size)` | 公告全文检索 | 巨潮(直连POST) | P |

## 调用示例

```bash
# 巨潮公告检索（默认每页 30，这里取 10）
asgk 资讯 announce 600519 --page-size 10
# 返回字段：date / type / title / url（公告详情页/PDF链接）
```

## 注意
- 巨潮用 **POST** 请求（不经网关，cninfo.com.cn 无IP风控）。
- 内含 orgId 动态映射（自动查官方映射表，规避 601xxx 段查不到公告的 bug）。
- 公告是事件定稿型（P档），单条一经发布永久不变。
