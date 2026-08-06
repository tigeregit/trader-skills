"""docs 能力服务端测试（mock 文档下载 + 验证 BinaryPayload/base64/doc_cache 流水线）。

验证文档型端到端：
  - announce_pdf：annoId → 查 adjunctUrl → 下载 → BinaryPayload → base64 响应
  - report_pdf：infoCode → 拼 URL → 下载 → BinaryPayload
  - doc_cache 命中（HIT-DOC）：第二次同 doc_id 不重新下载
  - 超单文件上限拒绝（20MB）

不打真实上游——mock egress_request 返回构造的 PDF bytes。
"""
from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

from asgk_server import registry
from asgk_server.server import CapabilityServer


@pytest.fixture(autouse=True)
def _keep_registry():
    yield


def _config(tmp_path) -> dict:
    return {
        "group": [{"name": "cninfo", "domains": ["cninfo.com.cn"], "rps": 100, "jitter": [0, 0]},
                  {"name": "eastmoney", "domains": ["eastmoney.com"], "rps": 100, "jitter": [0, 0]}],
        "circuit": {"cooldown_seconds": 300, "failure_threshold": 3, "probe_lease_seconds": 120},
        "state": {"enabled": False},
        "retry": {"max_attempts": 1},
        "cache": {"session": {"intraday_start": "09:00", "intraday_end": "18:00"},
                  "persist": {"enabled": True, "dir": str(tmp_path / "cache")}},
        "fingerprint": {"enabled": False},
    }


@pytest.fixture
def srv(tmp_path) -> CapabilityServer:
    s = CapabilityServer(_config(tmp_path))
    yield s
    s.close()


def _pdf_bytes(size: int = 100) -> bytes:
    """构造最小合法 PDF bytes（%PDF- 头 + 填充）。"""
    return b"%PDF-1.7\n" + b"x" * (size - 9)


def _get_resp(content: bytes, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.content = content
    return r


class TestReportPdf:
    def test_report_pdf_returns_base64(self, srv):
        """report_pdf：infoCode → 下载 → base64 响应。"""
        pdf = _pdf_bytes(100)
        with patch("asgk_server.capabilities.docs.egress_request",
                   return_value=_get_resp(pdf)):
            status, payload = srv.handle_capability(
                "docs", {"doc_type": "report_pdf", "info_code": "AP202607231",
                         "source": "eastmoney"})
        assert status == 200
        assert payload["_binary"] is True
        assert payload["ext"] == "pdf"
        assert payload["content_type"] == "application/pdf"
        # base64 解码回原 bytes
        assert base64.b64decode(payload["data"]) == pdf

    def test_report_pdf_cache_hit_second_call(self, srv):
        """同 info_code 第二次命中 doc_cache（不重新下载）。"""
        pdf = _pdf_bytes(100)
        with patch("asgk_server.capabilities.docs.egress_request",
                   return_value=_get_resp(pdf)) as mock_egress:
            srv.handle_capability("docs",
                {"doc_type": "report_pdf", "info_code": "AP1", "source": "eastmoney"})
            srv.handle_capability("docs",
                {"doc_type": "report_pdf", "info_code": "AP1", "source": "eastmoney"})
        # 只下载一次（第二次命中 doc_cache）
        assert mock_egress.call_count == 1

    def test_report_pdf_missing_info_code_returns_none(self, srv):
        """缺 info_code → None（HTTP 200 但 data None）。"""
        status, payload = srv.handle_capability(
            "docs", {"doc_type": "report_pdf", "source": "eastmoney"})
        # fetch 返回 None（ctx 未失败）→ 200 + data None
        assert status == 200
        assert payload["data"] is None

    def test_report_pdf_rejects_oversize(self, srv):
        """超 20MB 文档拒绝（返 None）。"""
        big = b"%PDF-" + b"x" * (21 * 1024 * 1024)
        with patch("asgk_server.capabilities.docs.egress_request",
                   return_value=_get_resp(big)):
            status, payload = srv.handle_capability(
                "docs", {"doc_type": "report_pdf", "info_code": "AP1",
                         "source": "eastmoney"})
        assert status == 200
        assert payload["data"] is None  # 拒绝，未写缓存


class TestAnnouncePdf:
    def test_announce_pdf_resolves_and_downloads(self, srv):
        """announce_pdf：annoId → 查 adjunctUrl → 下载 PDF。"""
        pdf = _pdf_bytes(100)
        # mock 两步：① _resolve_announce_adjunct 返 adjunctUrl；② 下载返 PDF
        with patch("asgk_server.capabilities.docs._resolve_announce_adjunct",
                   return_value="finalpage/2026-01-01/123.PDF"), \
             patch("asgk_server.capabilities.docs.egress_request",
                   return_value=_get_resp(pdf)):
            status, payload = srv.handle_capability(
                "docs", {"doc_type": "announce_pdf", "anno_id": "123",
                         "code": "600519"})
        assert status == 200
        assert payload["_binary"] is True
        assert base64.b64decode(payload["data"]) == pdf

    def test_announce_pdf_missing_params_returns_none(self, srv):
        """缺 anno_id 或 code → None。"""
        status, payload = srv.handle_capability(
            "docs", {"doc_type": "announce_pdf", "anno_id": "123"})
        assert status == 200
        assert payload["data"] is None
