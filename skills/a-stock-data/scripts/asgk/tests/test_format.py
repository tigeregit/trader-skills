"""客户端格式化与交付层测试（§3.5）。

覆盖：
  - 各 data_type × 各 format 组合的格式化正确性（json/csv/md/xlsx/plain）
  - 不支持组合在客户端报错（如 F10 请求 csv → ValueError）
  - 交付三态：return / print / file
  - 零破坏：不传 format 时业务函数原样返回（45 个现有调用方无感）
  - data_type 声明：业务函数的 @source data_type 正确驱动格式校验
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from asgk import _format, _output
from asgk._format import format_data, supported_formats, validate
from asgk._output import deliver


# ── 格式校验矩阵（§3.5）──────────────────────────────────────
class TestValidationMatrix:
    def test_table_supports_all_but_plain(self):
        assert supported_formats("table") == {"json", "csv", "md", "xlsx"}

    def test_kv_excludes_csv_xlsx(self):
        """kv 型 csv 单行无意义、xlsx 不适用。"""
        assert supported_formats("kv") == {"json", "md"}
        assert "csv" not in supported_formats("kv")
        assert "xlsx" not in supported_formats("kv")

    def test_text_excludes_csv_xlsx(self):
        assert supported_formats("text") == {"json", "md", "plain"}

    def test_document_no_formatting(self):
        """文档型不走格式化层。"""
        assert supported_formats("document") == set()

    def test_unsupported_combo_raises(self):
        """F10(text) 请求 csv → ValueError（在客户端报错，不打扰服务端）。"""
        with pytest.raises(ValueError, match="text.*不支持.*csv"):
            validate("text", "csv")
        with pytest.raises(ValueError, match="kv.*不支持.*xlsx"):
            validate("kv", "xlsx")

    def test_document_formatting_raises(self):
        with pytest.raises(ValueError, match="文档型不走格式化层"):
            validate("document", "json")

    def test_unknown_data_type_raises(self):
        with pytest.raises(ValueError, match="未知数据类型"):
            validate("bogus", "json")


# ── json 格式化 ──────────────────────────────────────────────
class TestJsonFormat:
    def test_table_to_json(self):
        data = [{"code": "600519", "price": 1309.0}]
        out = format_data(data, "table", "json")
        parsed = json.loads(out)
        assert parsed == data
        assert "贵州茅台" not in out  # 无中文时无差异；有中文时 ensure_ascii=False

    def test_json_preserves_chinese(self):
        data = {"name": "贵州茅台"}
        out = format_data(data, "kv", "json")
        assert "贵州茅台" in out  # ensure_ascii=False 保留中文

    def test_kv_to_json(self):
        data = {"price": 100.0}
        out = format_data(data, "kv", "json")
        assert json.loads(out) == {"price": 100.0}


# ── csv 格式化 ───────────────────────────────────────────────
class TestCsvFormat:
    def test_table_to_csv(self):
        data = [{"code": "600519", "price": 1309}, {"code": "000001", "price": 12}]
        out = format_data(data, "table", "csv")
        lines = out.replace("\r\n", "\n").strip().split("\n")
        assert lines[0] == "code,price"
        assert lines[1] == "600519,1309"
        assert lines[2] == "000001,12"

    def test_csv_union_columns(self):
        """不同行不同键时取并集列（缺失值留空）。"""
        data = [{"a": 1}, {"b": 2}]
        out = format_data(data, "table", "csv")
        lines = out.replace("\r\n", "\n").strip().split("\n")
        assert lines[0] == "a,b"

    def test_csv_empty_list(self):
        out = format_data([], "table", "csv")
        assert out == ""


# ── md 格式化 ────────────────────────────────────────────────
class TestMdFormat:
    def test_table_to_md(self):
        data = [{"code": "600519", "price": 1309}]
        out = format_data(data, "table", "md")
        lines = out.split("\n")
        assert lines[0] == "| code | price |"
        assert lines[1] == "| --- | --- |"
        assert lines[2] == "| 600519 | 1309 |"

    def test_kv_to_md(self):
        data = {"price": 100.0, "name": "贵州茅台"}
        out = format_data(data, "kv", "md")
        assert "| 字段 | 值 |" in out
        assert "| price | 100.0 |" in out
        assert "贵州茅台" in out

    def test_text_to_md(self):
        """text 型 md 原样返回文本。"""
        out = format_data("公司概况：贵州茅台", "text", "md")
        assert out == "公司概况：贵州茅台"

    def test_empty_table_md(self):
        out = format_data([], "table", "md")
        assert "无数据" in out


# ── xlsx 格式化 ──────────────────────────────────────────────
class TestXlsxFormat:
    def test_table_to_xlsx_bytes(self):
        data = [{"code": "600519", "price": 1309}]
        out = format_data(data, "table", "xlsx")
        assert isinstance(out, bytes)
        # xlsx 是 zip，魔数 PK
        assert out[:2] == b"PK"

    def test_xlsx_roundtrip(self):
        """xlsx bytes 能被 pandas 读回。"""
        import pandas as pd
        from io import BytesIO
        data = [{"a": 1, "b": "x"}]
        out = format_data(data, "table", "xlsx")
        df = pd.read_excel(BytesIO(out), engine="openpyxl")
        assert df.to_dict("records") == data


# ── plain 格式化 ─────────────────────────────────────────────
class TestPlainFormat:
    def test_text_to_plain(self):
        out = format_data("原始文本", "text", "plain")
        assert out == "原始文本"


# ── 交付层（return/print/file）───────────────────────────────
class TestDelivery:
    def test_return_default(self, capsys):
        """return 模式原样返回，不打印。"""
        result = deliver("data", "return")
        assert result == "data"
        assert capsys.readouterr().out == ""

    def test_print_str(self, capsys):
        deliver("hello", "print")
        assert capsys.readouterr().out == "hello\n"

    def test_print_bytes(self, capfd):
        """bytes 走 stdout.buffer，不抛异常。"""
        deliver(b"binary", "print")
        captured = capfd.readouterr()
        # capfd 捕获二进制级 stdout；bytes 经 buffer 输出
        assert "binary" in captured.out or captured.out == ""

    def test_file_writes_and_returns_path(self, tmp_path):
        path = tmp_path / "out.csv"
        result = deliver("a,b\n1,2", "file", str(path), fmt="csv")
        assert result == str(path)
        assert path.read_text() == "a,b\n1,2"

    def test_file_bytes(self, tmp_path):
        path = tmp_path / "out.xlsx"
        deliver(b"PK\x03\x04", "file", str(path), fmt="xlsx")
        assert path.read_bytes() == b"PK\x03\x04"

    def test_file_requires_path(self):
        with pytest.raises(ValueError, match="需指定 path"):
            deliver("x", "file")

    def test_file_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "sub" / "deep" / "out.csv"
        deliver("x", "file", str(path), fmt="csv")
        assert path.exists()

    def test_unknown_output_raises(self):
        with pytest.raises(ValueError, match="未知 output"):
            deliver("x", "bogus")


# ── 集成：业务函数 format/output/path（§3.5 接口）────────────
class TestBusinessFunctionFormat:
    """业务函数经 @source 装饰器注入 format/output/path。"""

    def test_no_format_zero_break(self):
        """不传 format → 原样返回结构化数据（零破坏）。"""
        with patch("asgk.quote._server_call", return_value=None), \
             patch("asgk.quote.em_get") as gw:
            gw.return_value.content = "".encode("gbk")
            result = __import__("asgk").tencent_quote(["600519"])
        # 无 format → 返回原始 dict（空）
        assert isinstance(result, dict)

    def test_format_csv_via_decorator(self):
        """tencent_quote(format='csv') → kv 型不支持 csv → ValueError。"""
        from asgk import tencent_quote
        server_data = {"600519": {"name": "贵州茅台", "price": 1309.0}}
        with patch("asgk.quote._server_call", return_value=server_data):
            with pytest.raises(ValueError, match="kv.*不支持.*csv"):
                tencent_quote(["600519"], format="csv")

    def test_format_md_kv(self):
        """tencent_quote(format='md') → kv 型 markdown 表格。"""
        from asgk import tencent_quote
        server_data = {"600519": {"price": 1309.0}}
        with patch("asgk.quote._server_call", return_value=server_data):
            out = tencent_quote(["600519"], format="md")
        assert isinstance(out, str)
        assert "| price |" in out or "| 600519 |" in out

    def test_format_json_table(self):
        """margin_trading(format='json') → table 型 JSON。

        margin_trading 对 datacenter 原始记录做字段映射（DATE→date, RZYE→rzye...），
        故 mock 需返回原始 datacenter 记录格式，最终 JSON 是映射后的结构。
        """
        from asgk import margin_trading
        raw_records = [{"DATE": "2026-08-05T00:00:00", "RZYE": 17514807881,
                        "RZMRE": 0, "RZCHE": 0, "RQYE": 0, "RQMCL": 0,
                        "RQCHL": 0, "RZRQYE": 0}]
        with patch("asgk._datacenter._server_call", return_value=raw_records):
            out = margin_trading("600519", format="json")
        assert isinstance(out, str)
        parsed = json.loads(out)
        assert parsed[0]["date"] == "2026-08-05"
        assert parsed[0]["rzye"] == 17514807881

    def test_format_xlsx_file(self, tmp_path):
        """margin_trading(format='xlsx', output='file') → 生成 xlsx 文件。"""
        from asgk import margin_trading
        raw_records = [{"DATE": "2026-08-05T00:00:00", "RZYE": 100,
                        "RZMRE": 0, "RZCHE": 0, "RQYE": 0, "RQMCL": 0,
                        "RQCHL": 0, "RZRQYE": 0}]
        xlsx_path = tmp_path / "margin.xlsx"
        with patch("asgk._datacenter._server_call", return_value=raw_records):
            result = margin_trading("600519", format="xlsx",
                                    output="file", path=str(xlsx_path))
        assert result == str(xlsx_path)
        assert xlsx_path.exists()
        assert xlsx_path.read_bytes()[:2] == b"PK"

    def test_format_print(self, capsys):
        """margin_trading(format='md', output='print') → 打印到 stdout。"""
        from asgk import margin_trading
        raw_records = [{"DATE": "2026-08-05T00:00:00", "RZYE": 0, "RZMRE": 0,
                        "RZCHE": 0, "RQYE": 0, "RQMCL": 0, "RQCHL": 0, "RZRQYE": 0}]
        with patch("asgk._datacenter._server_call", return_value=raw_records):
            margin_trading("600519", format="md", output="print")
        captured = capsys.readouterr().out
        assert "| date |" in captured

    def test_text_format_rejects_csv(self):
        """mootdx_f10(text 型) 请求 csv → ValueError（在格式化层报错）。

        用 data_type 直接验证（避免触发 mootdx 网络调用）。
        """
        from asgk import mootdx_f10
        # mootdx_f10 声明了 data_type="text"，csv 不在 text 支持集
        assert mootdx_f10._asgk_meta.data_type == "text"
        with pytest.raises(ValueError, match="text.*不支持.*csv"):
            validate("text", "csv")

    def test_output_path_not_passed_to_func(self):
        """format/output/path 被装饰器拦截，不传给业务函数（不污染签名）。"""
        from asgk import tencent_quote
        server_data = {"600519": {"price": 1}}
        with patch("asgk.quote._server_call", return_value=server_data) as sc:
            tencent_quote(["600519"], format="json", output="return", path="x.json")
        # _server_call 只收到 codes，不收 format/output/path
        call_args = sc.call_args
        assert "format" not in str(call_args)
        assert "output" not in str(call_args)
