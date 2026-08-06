"""asgk._output — 交付层（§3.5）：return / print / file 三态。

格式化（_format.py）之后的交付控制：
  - return（默认）：返回格式化结果（str/bytes）或原始 Python 对象（未格式化）
  - print：打印到 stdout
  - file：写入文件，返回路径

与格式化解耦：output 只管"格式化结果怎么交付"，format 只管"数据怎么变格式"。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

Output = Literal["return", "print", "file"]


def deliver(data: Any, output: Output, path: str | None = None,
            fmt: str | None = None) -> Any:
    """按 output 模式交付数据。

    Args:
        data: 待交付的数据（格式化后的 str/bytes，或未格式化的原始对象）
        output: return / print / file
        path: output='file' 时的目标路径（必填）
        fmt: 当前格式（仅用于决定 file 模式的写入模式：xlsx=二进制，其余=文本）
    Returns:
        return 模式：原样返回 data
        print 模式：打印后返回 None
        file 模式：写入文件后返回路径字符串
    """
    if output == "return":
        return data
    if output == "print":
        _print(data)
        return None
    if output == "file":
        if not path:
            raise ValueError("output='file' 需指定 path")
        return _write_file(data, path, fmt)
    raise ValueError(f"未知 output 模式: {output!r}，支持 return/print/file")


def _print(data: Any) -> None:
    """打印到 stdout（bytes 走 buffer，str/其他走 text）。"""
    if isinstance(data, bytes):
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.write(b"\n")
        sys.stdout.buffer.flush()
    elif isinstance(data, str):
        print(data)
    else:
        print(data)


def _write_file(data: Any, path: str, fmt: str | None) -> str:
    """写入文件，返回路径。xlsx(bytes) 二进制写，其余文本写。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(str(data), encoding="utf-8")
    return str(p)
