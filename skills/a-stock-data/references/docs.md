# 文档层（公告/研报 PDF 原文下载）

文档型能力——下载公告/研报的 PDF 原文（bytes），不同于结构化数据（dict/list）。

## 函数速查

| 函数 | 数据 | 源 | 说明 |
|------|------|----|----|
| `announce_pdf(anno_id, code)` | 巨潮公告 PDF 原文 | cninfo | annoId + code（解析 orgId 查 adjunctUrl） |
| `report_pdf(info_code)` | 东财研报 PDF 原文 | eastmoney | infoCode → pdf.dfcfw.com 直链 |

## 调用示例

```python
from asgk import announce_pdf, report_pdf, cninfo_announcements, eastmoney_reports

# 1. 先拿公告列表（含 annoId）
ann = cninfo_announcements("600519", page_size=3)
anno_id = ann[0]["url"].split("annoId=")[-1]   # 从 url 提取 annoId

# 2. 下载 PDF 原文（返回 bytes）
pdf = announce_pdf(anno_id, "600519")
with open("announce.pdf", "wb") as f:
    f.write(pdf)                                # %PDF- 开头

# 1. 先拿研报列表（含 infoCode）
reports = eastmoney_reports("600519")
info_code = reports[0]["infoCode"]

# 2. 下载研报 PDF
pdf = report_pdf(info_code)
```

CLI（文档型强制 `--output file`）：

```bash
python -m asgk announce_pdf 1225431263 600519 --output file --path anno.pdf
python -m asgk report_pdf AP202607231827290069 --output file --path report.pdf
```

## 注意

- **文档型与结构化数据不同**：返回原始 bytes（`%PDF-` 开头），不经格式化层（不能
  `--format csv`），只支持 file 交付。
- **服务端缓存**：同 annoId/infoCode 第二次命中服务端 DocumentCache（bytes 文件 +
  30 天 TTL + LRU 淘汰），不重新下载。
- **体积上限**：单文件 ≤20MB（超限拒绝），总文档缓存 ≤2GB（LRU 淘汰最久未访问的）。
- `announce_pdf` 需 `code`（服务端用它解析 orgId 查 adjunctUrl——cninfo 无 id→PDF 直链）。
- 二进制传输：bytes 无法经 JSON-RPC，服务端 base64 编码 + `_binary:true` 标记，
  客户端 `_server_call_binary` 解码。
