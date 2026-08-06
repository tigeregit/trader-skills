"""tests for asgk_server.cli — CLI 命令发现/参数绑定/HTTP 调用/格式化。

不连真实服务端：mock cli.client.call / call_binary / query_sources。
覆盖：
  - 命令发现（9 大类、64 子命令、by_category/find 索引）
  - 参数绑定（位置参数、多值、--flag、类型转换）
  - 结构化能力调用（POST /v1/<capability>）
  - 文档型调用（base64 解码 + 写盘）
  - 纯本地计算（PEG 等）
  - --sources 查源
  - 格式化层（json/csv/md/xlsx 矩阵）
  - 退出码（缺参数/未知命令）
"""
from __future__ import annotations

import base64

import pytest

from asgk_server.cli import (
    _bind_args,
    _build_parser,
    find,
    main,
)
from asgk_server.cli import format as fmt
from asgk_server.cli.commands import COMMANDS, by_category


# ── 命令发现 ──────────────────────────────────────────────────
class TestCommandDiscovery:
    def test_nine_categories(self):
        cats = sorted(by_category().keys())
        # 9 大类（sorted 按 Unicode 码点排序）
        assert cats == ['事件', '信号', '基本面', '研报', '行情', '衍生',
                        '资讯', '资金', '风控']
        assert len(cats) == 9

    def test_command_count(self):
        total = sum(len(cmds) for cmds in by_category().values())
        assert total == len(COMMANDS)
        assert total >= 60  # 64 个子命令

    def test_find_existing(self):
        cmd = find("行情", "realtime")
        assert cmd is not None
        assert cmd.capability == "quote"
        assert cmd.data_type == "kv"

    def test_find_missing_returns_none(self):
        assert find("行情", "nonexistent") is None
        assert find("不存在", "realtime") is None

    def test_all_commands_have_required_fields(self):
        for cmd in COMMANDS:
            assert cmd.category, f"{cmd.name} 缺 category"
            assert cmd.name, f"{cmd.category} 有空 name"
            assert cmd.help, f"{cmd.category} {cmd.name} 缺 help"
            # 非 local 必须有 capability
            if not cmd.local:
                assert cmd.capability, f"{cmd.name} 缺 capability"
            # local 必须有 local_fn
            if cmd.local:
                assert cmd.local_fn, f"{cmd.name} 缺 local_fn"

    def test_local_commands(self):
        """3 个纯计算命令不调服务端。"""
        locals_ = [c for c in COMMANDS if c.local]
        assert len(locals_) == 3
        names = {c.name for c in locals_}
        assert names == {"fwd_pe", "digest", "peg"}


# ── 参数绑定 ──────────────────────────────────────────────────
class TestArgBinding:
    def _parse(self, *argv):
        ap = _build_parser()
        return ap.parse_args(list(argv))

    def test_single_value_positional(self):
        args = self._parse("行情", "realtime", "600519")
        cmd = find("行情", "realtime")
        kwargs = _bind_args(cmd, args)
        assert kwargs == {"codes": ["600519"]}

    def test_multi_value_positional(self):
        args = self._parse("行情", "realtime", "600519", "000858")
        cmd = find("行情", "realtime")
        kwargs = _bind_args(cmd, args)
        assert kwargs == {"codes": ["600519", "000858"]}

    def test_optional_flag(self):
        args = self._parse("基本面", "report", "600519", "--num", "3")
        cmd = find("基本面", "report")
        kwargs = _bind_args(cmd, args)
        assert kwargs["code"] == "600519"
        assert kwargs["num"] == 3  # int 类型转换

    def test_numeric_type_conversion(self):
        """纯计算命令的数字参数被转为 float。"""
        args = self._parse("研报", "peg", "25", "0.2")
        cmd = find("研报", "peg")
        kwargs = _bind_args(cmd, args)
        assert kwargs["pe"] == 25.0
        assert kwargs["cagr"] == 0.2

    def test_fixed_params_not_in_args(self):
        """CmdSpec.fixed 是固定参数，不暴露给 argparse。"""
        args = self._parse("行情", "bars", "600519")
        cmd = find("行情", "bars")
        # bars 的 fixed={"mootdx_type":"bars"} 不应在 namespace 里
        assert not hasattr(args, "mootdx_type")


# ── 结构化能力调用（mock client.call）──────────────────────────
class TestStructuredCall:
    def test_kv_default_md(self, capsys, monkeypatch):
        monkeypatch.setattr(
            "asgk_server.cli.call",
            lambda cap, params, **kw: {"600519": {"name": "茅台", "price": 1500}},
        )
        rc = main(["行情", "realtime", "600519"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "茅台" in out
        assert "|" in out  # md 表格

    def test_json_format(self, capsys, monkeypatch):
        monkeypatch.setattr(
            "asgk_server.cli.call",
            lambda cap, params, **kw: [{"code": "600519"}],
        )
        rc = main(["资讯", "telegraph", "--format", "json"])
        assert rc == 0
        out = capsys.readouterr().out
        assert '"code": "600519"' in out

    def test_capability_and_params_passed(self, monkeypatch):
        """验证 CLI 把正确的 capability + fixed + 位置参数传给 client.call。"""
        captured = {}
        def fake_call(cap, params, **kw):
            captured["cap"] = cap
            captured["params"] = params
            return []
        monkeypatch.setattr("asgk_server.cli.call", fake_call)
        main(["行情", "bars", "600519", "--frequency", "5"])
        assert captured["cap"] == "mootdx"
        assert captured["params"]["mootdx_type"] == "bars"  # fixed
        assert captured["params"]["code"] == "600519"        # 位置
        assert captured["params"]["frequency"] == 5          # flag

    def test_server_error_returns_nonzero(self, capsys, monkeypatch):
        from asgk_server.cli.client import ServerError
        monkeypatch.setattr(
            "asgk_server.cli.call",
            lambda cap, params, **kw: (_ for _ in ()).throw(
                ServerError("服务端返回 502")),
        )
        rc = main(["行情", "realtime", "600519"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "502" in err


# ── 文档型调用 ────────────────────────────────────────────────
class TestDocumentCall:
    def test_pdf_writes_file(self, tmp_path, monkeypatch):
        pdf_bytes = b"%PDF-1.4 fake content"
        b64 = base64.b64encode(pdf_bytes).decode()
        monkeypatch.setattr(
            "asgk_server.cli.call_binary",
            lambda cap, params, **kw: (pdf_bytes, "pdf"),
        )
        out = tmp_path / "anno.pdf"
        rc = main(["衍生", "announce_pdf", "123456", "600519",
                   "--output", "file", "--path", str(out)])
        assert rc == 0
        assert out.read_bytes() == pdf_bytes

    def test_doc_requires_file_output(self, capsys, monkeypatch):
        rc = main(["衍生", "announce_pdf", "123", "600519"])
        assert rc == 1
        assert "file" in capsys.readouterr().err


# ── 纯本地计算 ────────────────────────────────────────────────
class TestLocalCall:
    def test_peg(self, capsys):
        rc = main(["研报", "peg", "25", "0.2"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "1.25" in out

    def test_fwd_pe(self, capsys):
        rc = main(["研报", "fwd_pe", "1500", "60"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "25" in out  # 1500/60=25

    def test_digest_with_target(self, capsys):
        rc = main(["研报", "digest", "40", "0.15", "--target-pe", "25"])
        assert rc == 0


# ── --sources ────────────────────────────────────────────────
class TestSourcesFlag:
    def test_sources_queries_server(self, capsys, monkeypatch):
        monkeypatch.setattr(
            "asgk_server.cli.query_sources",
            lambda **kw: ["tencent", "sina"],
        )
        rc = main(["行情", "realtime", "--sources"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "tencent" in out

    def test_sources_passes_capability(self, monkeypatch):
        captured = {}
        def fake(**kw):
            captured.update(kw)
            return []
        monkeypatch.setattr("asgk_server.cli.query_sources", fake)
        main(["行情", "realtime", "--sources"])
        assert captured.get("capability") == "quote"


# ── 退出码 / 错误处理 ─────────────────────────────────────────
class TestExitCodes:
    def test_no_command_shows_list(self, capsys):
        rc = main([])
        assert rc == 0
        out = capsys.readouterr().out
        assert "行情" in out
        assert "大类" in out

    def test_missing_required_arg(self, capsys):
        rc = main(["行情", "realtime"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "缺少必填参数" in err
        assert "codes" in err

    def test_list_flag(self, capsys):
        rc = main(["--list"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "子命令" in out


# ── 格式化层 ──────────────────────────────────────────────────
class TestFormat:
    def test_table_to_md(self):
        data = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        md = fmt.format_data(data, "table", "md")
        assert "| a | b |" in md
        assert "| 1 | x |" in md

    def test_kv_to_md(self):
        md = fmt.format_data({"k": "v"}, "kv", "md")
        assert "| 字段 | 值 |" in md
        assert "| k | v |" in md

    def test_to_json(self):
        j = fmt.format_data({"k": "v"}, "kv", "json")
        assert '"k": "v"' in j

    def test_to_csv(self):
        csv = fmt.format_data([{"a": 1}], "table", "csv")
        assert "a" in csv
        assert "1" in csv

    def test_validate_rejects_bad_combo(self):
        with pytest.raises(ValueError):
            fmt.validate("kv", "csv")  # kv 不支持 csv

    def test_validate_rejects_doc(self):
        with pytest.raises(ValueError):
            fmt.validate("doc", "json")  # doc 不走格式化层

    def test_default_format(self):
        assert fmt.default_format("table") == "md"
        assert fmt.default_format("kv") == "md"

    def test_empty_list_md(self):
        assert "无数据" in fmt.format_data([], "table", "md")


# ── 映射表与服务端 registry 一致性 ────────────────────────────
class TestRegistryConsistency:
    def test_mapped_capabilities_exist_in_server(self):
        """CLI 映射表引用的 capability 必须在服务端注册（防漂移）。

        服务端 registry 在 import asgk_server.capabilities 时填充。
        """
        from asgk_server import capabilities  # noqa: F401  触发注册
        from asgk_server.registry import list_capabilities

        server_caps = set(list_capabilities().keys())
        cli_caps = {c.capability for c in COMMANDS if not c.local}
        missing = cli_caps - server_caps
        assert not missing, f"CLI 引用了服务端未注册的能力: {missing}"
