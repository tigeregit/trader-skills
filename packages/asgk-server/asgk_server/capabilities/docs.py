"""docs 能力 — 文档下载（公告/研报 PDF 原文）。

当前项目完全缺失的能力（announce/reports 只返回 url，不下载原文）。文档型与
结构化数据性质不同：
  - 返回原始 bytes（BinaryPayload 标记），不经 JSON-RPC 的结构化路径
  - 走 DocumentCache（bytes 文件 + 20MB/2GB 上限 + LRU 淘汰），不走 SemanticCache
  - 服务端 HTTP 响应 base64 编码：{"data":"<b64>","_binary":true,"ext":"pdf",...}

两个变体（doc_type 参数区分）：
  - announce_pdf：巨潮公告 PDF。annoId → 查公告列表拿 adjunctUrl →
    static.cninfo.com.cn/{adjunctUrl} 下载。需 code 解析 orgId（cninfo 无 id→PDF 直链）。
  - report_pdf：东财研报 PDF。infoCode → pdf.dfcfw.com/pdf/H3_{infoCode}_1.pdf 直接下载。

客户端发 {doc_type, anno_id/code, info_code}，服务端返回 BinaryPayload。
"""
from __future__ import annotations

from typing import Any

from ..binary import BinaryPayload
from ..context import FetchContext
from ..egress import egress_request
from ..registry import SourceMeta, capability

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"
_ANNO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_CNINFO_STATIC = "http://static.cninfo.com.cn/"
_EM_REPORT_PDF = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"
# 单文件上限 20MB（与 DocumentCache.MAX_FILE_BYTES 一致）
_MAX_DOC_BYTES = 20 * 1024 * 1024


def _download(ctx: FetchContext, url: str, headers: dict | None = None,
              timeout: int = 60) -> bytes | None:
    """下载文档 bytes（限流 + 熔断反馈）。超 20MB 拒绝（文档型体积保护）。

    失败/超限返回 None。
    """
    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    if not ctx.acquire():
        return None
    try:
        r = egress_request("get", ctx.source.egress_client, url,
                           headers=h, timeout=timeout)
    except Exception:
        ctx.on_network_error()
        return None
    if r.status_code in (403, 429):
        ctx.on_failure(status=r.status_code, immediate=True)
        return None
    if r.status_code >= 500:
        ctx.on_failure(status=r.status_code)
        return None
    ctx.on_success()
    if len(r.content) > _MAX_DOC_BYTES:
        return None  # 超 20MB 拒绝（不写缓存也不返回）
    return r.content


def _resolve_announce_adjunct(ctx: FetchContext, anno_id: str,
                              code: str) -> str | None:
    """查巨潮公告列表拿 annoId 对应的 adjunctUrl（PDF 相对路径）。

    cninfo 无 id→PDF 直链 API，需按 code+orgId 查公告列表匹配 announcementId。
    """
    from .cninfo import _get_orgid, _post
    org_id = _get_orgid(ctx, code)
    d = _post(ctx, _ANNO_QUERY,
              data={"stock": f"{code},{org_id}", "tabName": "fulltext",
                    "pageSize": "100", "pageNum": "1",
                    "column": "", "category": "", "plate": "", "seDate": "",
                    "searchkey": "", "secid": "", "sortName": "", "sortType": "",
                    "isHLtitle": "true"},
              headers={"Referer": "https://www.cninfo.com.cn/new/disclosure",
                       "Origin": "https://www.cninfo.com.cn"})
    if d is None:
        return None
    for item in d.get("announcements", []) or []:
        if str(item.get("announcementId")) == str(anno_id):
            return item.get("adjunctUrl")
    return None


@capability(
    name="docs",
    domain="文档",
    sources=[SourceMeta(name="cninfo", group="cninfo"),
             SourceMeta(name="eastmoney", group="eastmoney")],
    default_source="cninfo",
    data_type="doc",  # 文档型（registry 的 _DATA_TYPES 含 doc）
    cache_policy="document",  # §3.7 第七类：30天 TTL + bytes 文件 + LRU
    supported_formats=["file"],  # 文档只支持文件交付（不走 table/csv 格式化）
)
def fetch_docs(ctx: FetchContext, doc_type: str, anno_id: str = "",
               code: str = "", info_code: str = "", **_unused) -> BinaryPayload | None:
    """文档下载。doc_type ∈ {announce_pdf, report_pdf}。

    announce_pdf: 巨潮公告 PDF。需 anno_id + code（解析 orgId 查 adjunctUrl）。
    report_pdf:   东财研报 PDF。需 info_code（直接拼 pdf.dfcfw.com URL）。
    返回 BinaryPayload(bytes, "pdf")；失败返回 None。
    """
    if doc_type == "announce_pdf":
        return _fetch_announce_pdf(ctx, anno_id, code)
    if doc_type == "report_pdf":
        return _fetch_report_pdf(ctx, info_code)
    return None


def _fetch_announce_pdf(ctx: FetchContext, anno_id: str,
                        code: str) -> BinaryPayload | None:
    """巨潮公告 PDF：annoId → adjunctUrl → static 下载。"""
    if not anno_id or not code:
        return None
    # 显式选 cninfo 源（announce 走 cninfo 组）
    adjunct = _resolve_announce_adjunct(ctx, anno_id, code)
    if not adjunct:
        return None
    url = _CNINFO_STATIC + adjunct
    data = _download(ctx, url)
    if data is None:
        return None
    return BinaryPayload(data=data, ext="pdf", content_type="application/pdf")


def _fetch_report_pdf(ctx: FetchContext, info_code: str) -> BinaryPayload | None:
    """东财研报 PDF：infoCode → pdf.dfcfw.com 直接下载。

    report_pdf 走 eastmoney 源（但 docs 能力 default_source=cninfo）。客户端应
    显式传 source="eastmoney"。这里按 source.egress_client 出网（ctx.source 已
    被 server 按请求的 source 解析好）。
    """
    if not info_code:
        return None
    url = _EM_REPORT_PDF.format(info_code=info_code)
    data = _download(ctx, url,
                     headers={"Referer": "https://data.eastmoney.com/"})
    if data is None:
        return None
    return BinaryPayload(data=data, ext="pdf", content_type="application/pdf")
