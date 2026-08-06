"""asgk.cli — 命令行入口（§3.3 CLI 双入口）。

用 registry() 自动发现声明了 cli= 的业务函数，注册为子命令。shell/其他语言
都能用，不必经 Python 库。

用法：
    asgk <command> [args] [--format json|csv|md|xlsx|plain]
                     [--output return|print|file] [--path PATH]
                     [--source SRC] [--sources]

命令发现：@source(cli="quote") 的函数注册为 `asgk quote`。
参数映射：位置参数按函数签名绑定（codes 型多值收集，code 型单值）；
          有默认值的参数暴露为 --flag。

示例：
    asgk quote 600519                      # 打印表格（默认）
    asgk quote 600519 --format json        # JSON 打印
    asgk quote --sources                   # 列出 quote 支持的源
    asgk announce 600519 --format csv --output file --path ann.csv
    asgk margin 600519 --page-size 50      # 可选参数 --flag
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
from typing import get_args, get_origin

from asgk._contract import registry
from asgk.em_proxy import _SERVER


def _is_list_param(annotation) -> bool:
    """参数注解是否为 list 型（如 list[str]）——决定是否收集多值位置参数。"""
    origin = get_origin(annotation)
    if origin is list:
        return True
    # 字符串形式的注解（from __future__ import annotations）需解析
    if isinstance(annotation, str) and annotation.startswith(("list[", "list[")):
        return True
    return False


def _list_annotation_name(annotation) -> str:
    """list[str] 参数的中文名（用于帮助文本），如 codes/list[str] → 'codes...'。"""
    return "多值"


def _cli_commands() -> dict[str, object]:
    """从 registry() 收集所有声明了 cli= 的函数，按 cli 名索引。"""
    cmds = {}
    for meta in registry():
        if meta.cli:
            cmds[meta.cli] = meta
    return cmds


def _build_subparser(subparsers, cli_name: str, meta) -> None:
    """为一个 cli 命令构建 argparse 子解析器。

    位置参数：函数签名中无默认值的参数（如 code/codes）。
    可选参数：有默认值的参数（如 page_size=30）暴露为 --page-size。
    """
    func = meta.func
    sig = inspect.signature(func)
    doc = (func.__doc__ or "").strip().split("\n")[0]  # 首行作 help
    sub = subparsers.add_parser(cli_name, help=doc, description=func.__doc__)

    for pname, param in sig.parameters.items():
        anno = param.annotation
        has_default = param.default is not inspect.Parameter.empty
        if _is_list_param(anno):
            # list 型参数：收集多值位置参数。用 nargs="*"（非"+"）让 --sources 可单独使用。
            sub.add_argument(pname.replace("_", "-"), nargs="*", default=None,
                             help=f"{pname}（多值）")
        elif has_default:
            # 有默认值：暴露为可选 --flag
            default = param.default
            if isinstance(default, bool):
                sub.add_argument(f"--{pname.replace('_', '-')}",
                                 type=lambda x: x.lower() in ("1", "true", "yes"),
                                 default=default)
            elif isinstance(default, int):
                sub.add_argument(f"--{pname.replace('_', '-')}", type=int,
                                 default=default)
            else:
                sub.add_argument(f"--{pname.replace('_', '-')}", default=default)
        else:
            # 必填位置参数（非 list）。用 nargs="?" 让 --sources 可单独使用，
            # 取数时若缺失则报错。
            sub.add_argument(pname.replace("_", "-"), nargs="?", default=None,
                             help=pname)

    # 格式化/交付/选源控制参数（全局，每个子命令都加）
    sub.add_argument("--format", default=None,
                     choices=["json", "csv", "md", "xlsx", "plain"],
                     help="输出格式（默认 table/json 视数据类型）")
    sub.add_argument("--output", default="print",
                     choices=["return", "print", "file"],
                     help="交付方式（CLI 默认 print）")
    sub.add_argument("--path", default=None, help="output=file 时的目标路径")
    sub.add_argument("--source", default=None, help="显式指定数据源")
    sub.add_argument("--sources", action="store_true",
                     help="列出该能力支持的源（查服务端 /v1/sources）")


def _bind_args(meta, args: argparse.Namespace) -> dict:
    """把 argparse Namespace 绑定为函数 kwargs（按签名）。

    argparse 把 --flag-name 存为属性 flag_name（- 转 _）；位置参数原名保留。
    绑定时按签名参数名（下划线形式）从 args 取值。
    """
    func = meta.func
    sig = inspect.signature(func)
    kwargs = {}
    for pname, param in sig.parameters.items():
        # argparse 属性名：位置参数原名，--flag 转 _。签名参数名用 _ 形式。
        if not hasattr(args, pname):
            continue
        val = getattr(args, pname)
        if val is None:
            continue
        # list 型：sig 参数名是 codes，argparse 存为 list
        if _is_list_param(param.annotation) and isinstance(val, list):
            kwargs[pname] = val
        else:
            kwargs[pname] = val
    return kwargs


def _query_sources(capability_hint: str) -> list[str] | dict | None:
    """查服务端 GET /v1/sources 列出能力支持的源。

    capability_hint 是 cli 名（如 quote）；尝试匹配业务函数所属能力。
    未配服务端时返回 None。
    """
    if not _SERVER:
        return None
    import requests
    try:
        r = requests.get(f"{_SERVER}/v1/sources", timeout=10)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。返回退出码。"""
    commands = _cli_commands()
    if not commands:
        print("未发现任何 CLI 命令（无 @source(cli=...) 声明）", file=sys.stderr)
        return 1

    ap = argparse.ArgumentParser(
        prog="asgk",
        description="A股数据 CLI（能力代理服务端取数 + 客户端格式化）",
    )
    subparsers = ap.add_subparsers(dest="command", required=True,
                                   metavar="<command>")
    for cli_name, meta in sorted(commands.items()):
        _build_subparser(subparsers, cli_name, meta)

    args = ap.parse_args(argv)
    meta = commands[args.command]

    # --sources：列出支持的源（查服务端），不取数
    if getattr(args, "sources", False):
        sources = _query_sources(args.command)
        if sources is None:
            print("（未配 ASGK_SERVER 或服务端不可达，无法列源）", file=sys.stderr)
            return 1
        print(json.dumps(sources, ensure_ascii=False, indent=2))
        return 0

    # 校验必填位置参数（nargs="?"/"*" 让 --sources 可单独用，取数时仍需提供）
    sig = inspect.signature(meta.func)
    missing = []
    for pname, param in sig.parameters.items():
        if param.default is inspect.Parameter.empty:  # 必填
            arg_name = pname.replace("_", "-")
            val = getattr(args, arg_name, None)
            if val is None or (isinstance(val, list) and not val):
                missing.append(arg_name)
    if missing:
        print(f"asgk {args.command}: 缺少必填参数: {', '.join(missing)}", file=sys.stderr)
        return 1

    # 绑定参数并调用业务函数（经 @source 装饰器走服务端 + 格式化）
    kwargs = _bind_args(meta, args)
    # 注入选源/格式化控制参数（装饰器会拦截）
    fmt = args.format
    # CLI 默认格式：未指定 format 时按数据类型选 md（表格友好）
    if fmt is None:
        fmt = "md" if meta.data_type in ("table", "kv", "series") else "json"
    kwargs["format"] = fmt
    kwargs["output"] = args.output
    if args.path:
        kwargs["path"] = args.path

    try:
        result = meta.wrapped(**kwargs)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    # output=return 时 CLI 也打印（否则用户看不到）
    if args.output == "return" and result is not None:
        if isinstance(result, bytes):
            sys.stdout.buffer.write(result)
        else:
            print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
