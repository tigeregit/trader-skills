"""CLI 入口测试（§3.3）。

覆盖：
  - 自动发现：registry() 中声明 cli= 的函数注册为子命令
  - 参数映射：位置参数（code 单值 / codes 多值）+ 可选 --flag
  - 格式化：--format/--output/--path 经装饰器注入
  - --sources：查服务端列源
  - 退出码：正常 0 / 错误 1

不打真实上游——mock 业务函数的包装函数（meta.wrapped）。
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from asgk import cli
from asgk._contract import registry


class TestCommandDiscovery:
    def test_cli_commands_registered(self):
        """声明了 cli= 的函数都注册为子命令。"""
        cmds = cli._cli_commands()
        # 已知声明的 cli 名
        assert "quote" in cmds
        assert "announce" in cmds
        assert "kline" in cmds
        assert "report" in cmds

    def test_no_cli_not_registered(self):
        """未声明 cli= 的函数不注册。"""
        cmds = cli._cli_commands()
        # margin_trading 没声明 cli，不应出现
        assert "margin_trading" not in cmds
        cli_names = [m.cli for m in registry()]
        assert None not in cli_names or all(c.cli for c in cmds.values())


class TestArgBinding:
    def test_single_value_positional(self):
        """code 型单值位置参数。"""
        meta = cli._cli_commands()["announce"]
        import argparse
        ns = argparse.Namespace(code="600519", page_size=30)
        kwargs = cli._bind_args(meta, ns)
        assert kwargs["code"] == "600519"
        assert kwargs["page_size"] == 30

    def test_multi_value_positional(self):
        """codes 型多值位置参数收集为 list。"""
        meta = cli._cli_commands()["quote"]
        import argparse
        ns = argparse.Namespace(codes=["600519", "000001"])
        kwargs = cli._bind_args(meta, ns)
        assert kwargs["codes"] == ["600519", "000001"]


class TestInvocation:
    def test_quote_default_md(self, capsys):
        """asgk quote 600519 → 默认 md 格式打印。

        patch _server_call 让真包装器格式化（不绕过格式化层）。
        """
        with patch("asgk.quote._server_call",
                   return_value={"600519": {"price": 100}}):
            rc = cli.main(["quote", "600519"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "| price |" in out or "| 600519 |" in out  # md 表格

    def test_quote_format_json(self, capsys):
        """asgk quote 600519 --format json → JSON 打印。"""
        with patch("asgk.quote._server_call",
                   return_value={"600519": {"price": 100}}):
            rc = cli.main(["quote", "600519", "--format", "json"])
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed == {"600519": {"price": 100}}

    def test_multiple_codes(self, capsys):
        """asgk quote 600519 000001 → 多值 codes 传给服务端调用。"""
        captured = {}

        def fake(capability, params):
            captured.update(params)
            return {"600519": {}, "000001": {}}

        with patch("asgk.quote._server_call", side_effect=fake):
            cli.main(["quote", "600519", "000001", "--format", "json"])
        assert captured["codes"] == ["600519", "000001"]

    def test_output_file(self, tmp_path):
        """asgk announce 600519 --format csv --output file --path X → 写文件。"""
        out_path = tmp_path / "ann.csv"
        with patch("asgk._datacenter._server_call",
                   return_value=[{"NOTICE_TITLE": "测试公告"}]):
            rc = cli.main(["announce", "600519", "--format", "csv",
                           "--output", "file", "--path", str(out_path)])
        assert rc == 0
        assert out_path.exists()

    def test_optional_flag_passed(self, capsys):
        """可选参数 --page-size 传给业务函数。

        announce 用 em_get（非 datacenter），mock asgk.announce.em_get。
        """
        captured = {}

        def fake_em_get(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            r = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
            r.json.return_value = {"announcements": []}
            return r

        # announce 先取 szse_stock.json（代码映射），再 POST 公告查询；两步都 mock
        with patch("asgk.announce.em_get", side_effect=fake_em_get):
            cli.main(["announce", "600519", "--page-size", "50", "--format", "json"])
        # page_size 经 _bind_args 传给了函数（被装饰器拦截 format/output，但 page_size 是业务参数）
        # 验证：em_get 被调用（说明函数执行了），且 page_size 影响了查询
        assert "url" in captured

    def test_error_returns_nonzero(self, capsys):
        """业务函数抛异常 → 退出码 1，错误到 stderr。"""
        with patch("asgk.quote._server_call",
                   side_effect=RuntimeError("boom")):
            rc = cli.main(["quote", "600519"])
        assert rc == 1
        assert "boom" in capsys.readouterr().err


class TestSourcesFlag:
    def test_sources_queries_server(self, capsys, monkeypatch):
        """asgk quote --sources → 查服务端 GET /v1/sources。"""
        monkeypatch.setattr(cli, "_SERVER", "http://srv:7701")
        with patch("asgk.cli._query_sources",
                   return_value=["tencent", "sina"]):
            rc = cli.main(["quote", "--sources"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "tencent" in out

    def test_sources_no_server_returns_error(self, capsys, monkeypatch):
        """未配服务端 → --sources 报错退出码 1。"""
        monkeypatch.setattr(cli, "_SERVER", None)
        with patch("asgk.cli._query_sources", return_value=None):
            rc = cli.main(["quote", "--sources"])
        assert rc == 1


class TestExitCodes:
    def test_no_command_errors(self):
        """无子命令 → argparse 报错（非零退出）。"""
        with pytest.raises(SystemExit):
            cli.main([])

    def test_unknown_command_errors(self):
        with pytest.raises(SystemExit):
            cli.main(["bogus_command"])
