"""asgk.docs — 文档下载层（公告/研报 PDF 原文）。

当前项目原本完全缺失的能力（announce/reports 只返回 url，不下载原文）。
文档型经能力代理服务端下载：服务端解析 PDF 直链 + 限流 + 熔断 + DocumentCache
（bytes 文件 + LRU），客户端零下载知识，只发 {doc_type, anno_id/code, info_code}。

实现约定：
  - announce_pdf：巨潮公告 PDF。需 anno_id + code（服务端解析 orgId 查 adjunctUrl）
  - report_pdf：东财研报 PDF。需 info_code（服务端拼 pdf.dfcfw.com URL）
  - 返回原始 bytes；调用方负责写文件或检查 %PDF- magic
  - 服务端未配/不可达 → 返回 None（文档型无旧路径可回退，是纯新能力）
  - @source data_type="document"：只支持 file 交付（不走 table/csv 格式化）
"""
from __future__ import annotations

from asgk._contract import source
from asgk.em_proxy import _server_call_binary


@source(tier="P", via="gateway", cli="announce_pdf", data_type="document")
def announce_pdf(anno_id: str, code: str) -> bytes | None:
    """巨潮公告 PDF 原文下载。

    Args:
        anno_id: 公告 ID（从 cninfo_announcements 返回的 url 里 annoId= 取）
        code: 6位股票代码（服务端用它解析 orgId 查 adjunctUrl——cninfo 无 id→PDF 直链）
    Returns:
        PDF 原始 bytes（%PDF- 开头），或 None（未配服务端/失败）。
    Note:
        文档型经能力代理服务端下载（服务端限流+熔断+DocumentCache 30天缓存）。
        同 anno_id 第二次命中服务端 cache（不重新下载）。
    """
    result = _server_call_binary("docs",
                                 {"doc_type": "announce_pdf",
                                  "anno_id": anno_id, "code": code})
    if result is None:
        return None
    data, _ext = result
    return data


@source(tier="P", via="gateway", cli="report_pdf", data_type="document")
def report_pdf(info_code: str) -> bytes | None:
    """东财研报 PDF 原文下载。

    Args:
        info_code: 研报 infoCode（从 eastmoney_reports 返回记录的 infoCode 字段取）
    Returns:
        PDF 原始 bytes（%PDF- 开头），或 None（未配服务端/失败）。
    Note:
        文档型经能力代理服务端下载（pdf.dfcfw.com 直链 + DocumentCache 缓存）。
    """
    result = _server_call_binary("docs",
                                 {"doc_type": "report_pdf",
                                  "info_code": info_code, "source": "eastmoney"})
    if result is None:
        return None
    data, _ext = result
    return data
