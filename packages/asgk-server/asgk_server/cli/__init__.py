"""asgk_server.cli — A股数据 CLI（纯 HTTP 客户端，9 大类两层命令）。

随 ``asgk-server`` 包一同安装（``uv tool install asgk-server`` 装出
``asgk-server`` + ``asgk`` 两个 bin）。CLI 不依赖任何业务函数库，直接
POST 服务端 ``/v1/<capability>`` 取数，本地只做参数绑定 + 格式化。

命令结构（9 大类 × 子命令）：

    asgk <大类> <子命令> [位置参数...] [--format json|csv|md|xlsx|plain]
                                  [--output return|print|file] [--path PATH]
                                  [--source SRC] [--sources]
                                  [--flag value ...]

示例：

    asgk quote realtime 600519                  # 茅台实时行情（默认 md）
    asgk quote realtime 600519 --format json    # JSON 格式
    asgk base report 600519                  # 财报三表
    asgk flow fundflow 600519 --format csv     # 120日资金流，CSV
    asgk deriv announce_pdf 1225431263 600519 --output file --path anno.pdf
    asgk report peg 25 0.2                       # PEG 纯计算（不调服务端）
    asgk --list                                # 列出全部 9 大类 × 子命令
    asgk quote --help                           # 查看该大类下子命令

服务端地址解析（见 cli/config.py）：环境变量 ASGK_SERVER > ~/.config/asgk/cli.toml
> 包内默认（http://127.0.0.1:7701）。
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

from . import format as fmt_mod
from .client import ServerError, call, call_binary, query_sources
from .commands import CmdSpec, by_category, find
from .config import resolve_server
from .local import LOCAL_FNS


def _build_parser() -> argparse.ArgumentParser:
    """构建两层 argparse：顶层选大类，二级选子命令。"""
    ap = argparse.ArgumentParser(
        prog="asgk",
        description="A股数据 CLI（经能力代理服务端 asgk-server 取数）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="服务端地址：export ASGK_SERVER=http://127.0.0.1:7701 覆盖；"
               "或见 ~/.config/asgk/cli.toml。用 `asgk --list` 看全部命令。",
    )
    ap.add_argument("--list", action="store_true",
                    help="列出全部 9 大类 × 子命令")
    sub = ap.add_subparsers(dest="category", metavar="<大类>",
                            help="9 categories: quote/base/report/flow/signal/event/risk/news/deriv")

    # 为每个大类建一个子解析器，其下再嵌套子命令
    for cat, cmds in sorted(by_category().items()):
        cat_parser = sub.add_parser(cat, help=f"{cat}（{len(cmds)} 个子命令）")
        cat_sub = cat_parser.add_subparsers(
            dest="command", metavar="<子命令>")
        for cmd in cmds:
            _build_cmd_parser(cat_sub, cmd)
    return ap


def _build_cmd_parser(parent: argparse._SubParsersAction, cmd: CmdSpec) -> None:
    """为一个子命令构建 argparse（位置参数 + --flag）。"""
    p = parent.add_parser(cmd.name, help=cmd.help, description=cmd.help)
    for arg in cmd.args:
        is_flag = not arg.required and not arg.positional
        # 位置参数（含可选位置参数）保留原名；--flag 用连字符形式
        cli_name = (arg.cli_name or arg.name).replace("_", "-") if is_flag else (arg.cli_name or arg.name)
        if arg.is_list:
            # list 型：收集多值位置参数（nargs="*" 让 --sources 可单独用）
            p.add_argument(cli_name, nargs="*", default=None,
                           help=f"{arg.desc}（多值）")
        elif is_flag:
            # 可选：暴露为 --flag
            default = arg.default
            if isinstance(default, bool):
                # bool 型用 --flag 1/0/true/false
                p.add_argument(f"--{cli_name}",
                               type=lambda x: x.lower() in ("1", "true", "yes"),
                               default=default, help=arg.desc)
            elif arg.type is not str:
                # 声明了数字类型（int/float）
                p.add_argument(f"--{cli_name}", type=arg.type,
                               default=default, help=arg.desc)
            else:
                p.add_argument(f"--{cli_name}", default=default, help=arg.desc)
        else:
            # 位置参数（保留原名；不带 nargs，避免多 ? 位置参数绑定混乱）。
            # 可选位置参数（positional=True）用 nargs="?" 让其可省略。
            # --sources 单独使用时，由 _run_command 提前拦截，不会走到必填校验。
            kwargs: dict = {"help": arg.desc}
            if arg.positional:
                kwargs["nargs"] = "?"
                kwargs["default"] = arg.default
            if arg.type is not str:
                kwargs["type"] = arg.type
            p.add_argument(cli_name, **kwargs)

    # 全局格式化/交付/选源控制参数
    p.add_argument("--format", default=None,
                   choices=["json", "csv", "md", "xlsx", "plain"],
                   help="输出格式（默认按数据类型 md/json）")
    p.add_argument("--output", default="print",
                   choices=["return", "print", "file"],
                   help="交付方式（CLI 默认 print）")
    p.add_argument("--path", default=None, help="output=file 时的目标路径")
    p.add_argument("--source", default=None, help="显式指定数据源")
    p.add_argument("--sources", action="store_true",
                   help="列出该能力支持的源（查服务端 GET /v1/sources）")


def _bind_args(cmd: CmdSpec, args: argparse.Namespace) -> dict:
    """把 argparse Namespace 绑定为能力的语义参数 dict。

    argparse 把 --flag-name 存为属性 flag_name（- 转 _）；位置参数按注册名。
    绑定时按 CmdSpec.args 声明的 name 取值。
    """
    kwargs: dict[str, Any] = {}
    for arg in cmd.args:
        # argparse 属性名：位置参数用 cli_name.replace("-","_")；--flag 同理
        attr = (arg.cli_name or arg.name).replace("-", "_")
        val = getattr(args, attr, None)
        if val is None:
            continue
        kwargs[arg.name] = val
    return kwargs


def _print_list() -> int:
    """asgk --list：打印全部 9 大类 × 子命令。"""
    for cat, cmds in sorted(by_category().items()):
        print(f"\n【{cat}】（{len(cmds)}）")
        for cmd in cmds:
            # 拼出位置参数签名
            sig_parts = []
            for arg in cmd.args:
                cli = (arg.cli_name or arg.name)
                if arg.is_list:
                    sig_parts.append(f"<{cli}...>")
                elif not arg.required:
                    sig_parts.append(f"[--{cli}]")
                else:
                    sig_parts.append(f"<{cli}>")
            sig = " ".join(sig_parts)
            tag = " [纯计算]" if cmd.local else ""
            print(f"  asgk {cat} {cmd.name} {sig}".rstrip())
            print(f"      {cmd.help}{tag}")
    print(f"\n共 {sum(len(c) for c in by_category().values())} 个子命令，"
          f"{len(by_category())} 大类。")
    print("\n服务端地址解析：ASGK_SERVER 环境变量 > "
          "~/.config/asgk/cli.toml > 默认 http://127.0.0.1:7701")
    return 0


def _run_command(cmd: CmdSpec, args: argparse.Namespace) -> int:
    """执行一个子命令：绑定参数 → 调服务端/本地 → 格式化 → 交付。"""
    # --sources：列源，不取数
    if getattr(args, "sources", False):
        try:
            sources = query_sources(capability=cmd.capability)
        except ServerError as e:
            print(f"查询源失败: {e}", file=sys.stderr)
            return 1
        print(sources)
        return 0

    # 校验必填位置参数（位置参数保留原名，--flag argparse 自动转下划线存属性）
    missing = []
    for arg in cmd.args:
        if arg.required:
            # 位置参数注册名含下划线（argparse 原样存）；--flag 已被 argparse 转下划线
            attr = (arg.cli_name or arg.name).replace("-", "_")
            val = getattr(args, attr, None)
            if val is None or (isinstance(val, list) and not val):
                missing.append((arg.cli_name or arg.name))
    if missing:
        print(f"asgk {cmd.category} {cmd.name}: 缺少必填参数: "
              f"{', '.join(missing)}", file=sys.stderr)
        return 1

    kwargs = _bind_args(cmd, args)

    # ── 文档型（doc）：二进制交付 ──
    if cmd.data_type == "doc":
        if args.output != "file":
            print("文档型必须用 --output file --path PATH", file=sys.stderr)
            return 1
        if not args.path:
            # 兜底：用第一个必填参数值作文件名
            first_val = next(iter(kwargs.values()), "doc")
            args.path = f"{first_val}.pdf"
        params = {**cmd.fixed, **kwargs}
        if args.source:
            params["source"] = args.source
        try:
            data, ext = call_binary(cmd.capability, params)
        except ServerError as e:
            print(f"错误: {e}", file=sys.stderr)
            return 1
        # ext 与 path 后缀不一致时补正
        path = args.path
        if not path.endswith(f".{ext}"):
            path = f"{path}.{ext}"
        written = fmt_mod.deliver(data, "file", path)
        print(f"已写入: {written}", file=sys.stderr)
        return 0

    # ── 编排型命令（先调服务端拿部分数据，再合并本地计算）──
    if cmd.orchestrator == "time_status":
        from .local import time_status, time_now
        # 先调服务端 calendar 拿今天的 trade_day 判定（失败则合并 None）
        trade_day_result = None
        today = time_now()["date"]
        try:
            trade_day_result = call("calendar",
                                    {"calendar_type": "trade_day", "date": today})
        except ServerError:
            pass  # 服务端不可达，status 仍返回时间+时段，trade_day 标未判定
        result = time_status(trade_day_result=trade_day_result)
        # 格式化 + 交付（与下方 local 分支共用）
        fmt = args.format or fmt_mod.default_format(cmd.data_type)
        try:
            formatted = fmt_mod.format_data(result, cmd.data_type, fmt)
        except ValueError as e:
            print(f"格式化错误: {e}", file=sys.stderr)
            return 1
        fmt_mod.deliver(formatted, args.output, args.path, fmt)
        return 0

    # ── 纯本地计算 ──
    if cmd.local:
        fn = LOCAL_FNS.get(cmd.local_fn)
        if fn is None:
            print(f"内部错误：未知 local_fn {cmd.local_fn!r}", file=sys.stderr)
            return 1
        try:
            result = fn(**kwargs)
        except (TypeError, ValueError) as e:
            print(f"计算错误: {e}", file=sys.stderr)
            return 1
    else:
        # ── 结构化能力：POST 服务端 ──
        params = {**cmd.fixed, **kwargs}
        # calendar trade_day：date 缺省时填今天
        if (cmd.capability == "calendar"
                and params.get("calendar_type") == "trade_day"
                and not params.get("date")):
            from datetime import datetime
            params["date"] = datetime.now().strftime("%Y-%m-%d")
        if args.source:
            params["source"] = args.source
        try:
            result = call(cmd.capability, params)
        except ServerError as e:
            print(f"错误: {e}", file=sys.stderr)
            return 1

    # ── 格式化 + 交付 ──
    fmt = args.format or fmt_mod.default_format(cmd.data_type)
    try:
        formatted = fmt_mod.format_data(result, cmd.data_type, fmt)
    except ValueError as e:
        print(f"格式化错误: {e}", file=sys.stderr)
        return 1
    fmt_mod.deliver(formatted, args.output, args.path, fmt)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。返回退出码。"""
    ap = _build_parser()
    args = ap.parse_args(argv)

    # --list 或无子命令
    if args.list or not args.category:
        return _print_list()

    # 找子命令（未提供时打印该大类帮助）
    cmd_name = getattr(args, "command", None)
    if not cmd_name:
        ap.parse_args([args.category, "--help"])
        return 0

    cmd = find(args.category, cmd_name)
    if cmd is None:
        print(f"未知子命令: {args.category} {cmd_name}", file=sys.stderr)
        return 1

    return _run_command(cmd, args)


if __name__ == "__main__":
    sys.exit(main())
