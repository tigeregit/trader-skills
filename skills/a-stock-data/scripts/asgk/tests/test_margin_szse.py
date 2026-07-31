"""asgk margin_detail_szse + _xlsx 单元测试。

验证深交所融资融券的 xlsx 解析、字段映射、千分位清洗。
mock em_get，_xlsx 用真实 openpyxl（纯本地解析，不打外网）。
字段名基于真机确认（2026-07-31）。
"""
from __future__ import annotations

from io import BytesIO
from unittest.mock import patch, MagicMock

from openpyxl import Workbook

from asgk._xlsx import parse_xlsx
from asgk.capital import margin_detail_szse, _to_num


def _make_xlsx_bytes() -> bytes:
    """构造深交所融资融券格式的 xlsx（用 openpyxl 生成）。"""
    wb = Workbook()
    ws = wb.active
    ws.append(["证券代码", "证券简称", "融资买入额(元)", "融资余额(元)",
               "融券卖出量(股/份)", "融券余量(股/份)", "融券余额(元)", "融资融券余额(元)"])
    ws.append(["000001", "平安银行", "162,164,240", "5,267,151,599",
               "64,900", "401,200", "4,998,952", "5,272,150,551"])
    ws.append(["000002", "万  科Ａ", "75,050,775", "3,394,430,579",
               "83,600", "2,777,200", "18,857,188", "3,413,287,767"])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _em_get_xlsx_resp(content: bytes) -> MagicMock:
    r = MagicMock()
    r.content = content
    return r


class TestToNum:
    def test_comma_thousands(self):
        assert _to_num("162,164,240") == 162164240.0

    def test_plain_number(self):
        assert _to_num("12345") == 12345.0

    def test_none(self):
        assert _to_num(None) is None

    def test_empty(self):
        assert _to_num("") is None

    def test_invalid(self):
        assert _to_num("abc") is None


class TestParseXlsx:
    def test_basic_parse(self):
        """不传 dtype 时，证券代码被解析为数字（openpyxl 默认行为）。"""
        rows = parse_xlsx(_make_xlsx_bytes())
        assert len(rows) == 2
        # openpyxl 把 "000001" 存为字符串但读回可能变 int 1（取决于写入方式）
        # 这里只验证行数和简称（金额字段），代码保零由 dtype 测试覆盖
        assert rows[0]["证券简称"] == "平安银行"

    def test_dtype_str_preserves_leading_zero(self):
        """证券代码保前导零（dtype=str）——这是生产路径（margin_detail_szse 用）。"""
        rows = parse_xlsx(_make_xlsx_bytes(), dtype={"证券代码": str})
        assert rows[0]["证券代码"] == "000001"
        assert rows[1]["证券代码"] == "000002"


class TestMarginDetailSzse:
    def test_field_mapping(self):
        xlsx = _make_xlsx_bytes()
        with patch("asgk.capital.em_get", return_value=_em_get_xlsx_resp(xlsx)) as upstream:
            result = margin_detail_szse("20250728")
        upstream.assert_called_once()
        assert len(result) == 2
        r = result[0]
        assert r["code"] == "000001"
        assert r["name"] == "平安银行"
        assert r["rz_buy"] == 162164240.0  # 千分位清洗
        assert r["rz_balance"] == 5267151599.0
        assert r["rq_sell"] == 64900.0
        assert r["rq_volume"] == 401200.0
        assert r["rq_balance"] == 4998952.0
        assert r["rzrq_balance"] == 5272150551.0
        assert result[1]["code"] == "000002"
        assert result[1]["name"] == "万  科Ａ"

    def test_referer_header(self):
        """Referer 必须传递（深交所必需）。"""
        xlsx = _make_xlsx_bytes()
        with patch("asgk.capital.em_get", return_value=_em_get_xlsx_resp(xlsx)) as m:
            margin_detail_szse("20250728")
        assert "szse.cn" in m.call_args.kwargs["headers"]["Referer"]

    def test_date_to_iso(self):
        """date YYYYMMDD → txtDate YYYY-MM-DD。"""
        xlsx = _make_xlsx_bytes()
        with patch("asgk.capital.em_get", return_value=_em_get_xlsx_resp(xlsx)) as m:
            margin_detail_szse("20250728")
        assert m.call_args.kwargs["params"]["txtDate"] == "2025-07-28"

    def test_showtype_xlsx(self):
        xlsx = _make_xlsx_bytes()
        with patch("asgk.capital.em_get", return_value=_em_get_xlsx_resp(xlsx)) as m:
            margin_detail_szse("20250728")
        assert m.call_args.kwargs["params"]["SHOWTYPE"] == "xlsx"

    def test_empty_xlsx_returns_empty(self):
        # 空表（只有表头）
        wb = Workbook()
        wb.active.append(["证券代码", "证券简称"])
        buf = BytesIO(); wb.save(buf)
        with patch("asgk.capital.em_get", return_value=_em_get_xlsx_resp(buf.getvalue())):
            result = margin_detail_szse("20250728")
        assert result == []
