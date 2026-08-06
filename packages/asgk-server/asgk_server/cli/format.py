"""asgk_server.cli.format — 格式化 + 交付层。

从旧客户端 ``_format.py`` + ``_output.py`` 移植纯逻辑，合并为一个模块。
无网络、无状态：把服务端返回的结构化数据按 data_type 格式化为 json/csv/md/xlsx/plain，
并控制交付方式（return/print/file）。

数据类型 → 支持格式矩阵：
  table（list[dict]）：json/csv/md/xlsx
  kv（dict）：          json/md
  series（K线/资金流）： json/csv/md/xlsx
  text（F10）：         json/md/plain
  doc（PDF）：          不走格式化层（原始 bytes 直交付）

不支持的组合在此报错，不打扰服务端。
"""
from __future__ import annotations

import csv
import io
import json as _json
import sys
from pathlib import Path
from typing import Any, Literal

Output = Literal["return", "print", "file"]

# 数据类型 → 支持格式
_SUPPORTED: dict[str, set[str]] = {
    "table": {"json", "csv", "md", "xlsx"},
    "kv": {"json", "md"},
    "series": {"json", "csv", "md", "xlsx"},
    "text": {"json", "md", "plain"},
    "doc": set(),  # 文档型不走格式化层
}


def supported_formats(data_type: str) -> set[str]:
    """该数据类型支持的格式集合。doc 型返回空集。"""
    return _SUPPORTED.get(data_type, set())


def default_format(data_type: str) -> str:
    """未指定 --format 时的默认格式。"""
    return "md" if data_type in ("table", "kv", "series", "text") else "json"


def validate(data_type: str, fmt: str) -> None:
    """校验 (data_type, fmt) 组合合法，否则 ValueError。"""
    allowed = _SUPPORTED.get(data_type)
    if allowed is None:
        raise ValueError(f"未知数据类型: {data_type!r}")
    if data_type == "doc":
        raise ValueError("文档型不走格式化层（原始 bytes 直交付）")
    if fmt not in allowed:
        raise ValueError(f"{data_type}型不支持 {fmt!r}，支持: {sorted(allowed)}")


def format_data(data: Any, data_type: str, fmt: str) -> str | bytes:
    """把结构化数据格式化为指定格式。"""
    validate(data_type, fmt)
    if fmt == "json":
        return _to_json(data)
    if fmt == "csv":
        return _to_csv(data)
    if fmt == "md":
        return _to_md(data)
    if fmt == "xlsx":
        return _to_xlsx(data)
    if fmt == "plain":
        return data if isinstance(data, str) else str(data)
    raise ValueError(f"未知格式: {fmt!r}")


def deliver(data: Any, output: Output, path: str | None = None,
            fmt: str | None = None) -> Any:
    """按 output 模式交付格式化后的数据。

    return  → 原样返回
    print   → 打印到 stdout（bytes 走 buffer）
    file    → 写盘，返回路径
    """
    if output == "return":
        return data
    if output == "print":
        if isinstance(data, bytes):
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.write(b"\n")
            sys.stdout.buffer.flush()
        else:
            print(data)
        return None
    if output == "file":
        if not path:
            raise ValueError("output='file' 需指定 path")
        return _write_file(data, path)
    raise ValueError(f"未知 output 模式: {output!r}")


# ── 各格式实现 ────────────────────────────────────────────────
def _to_json(data: Any) -> str:
    return _json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _to_csv(data: Any) -> str:
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = [data]
    else:
        rows = [{"value": data}]
    if not rows:
        return ""
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            for k in row:
                if k not in seen:
                    columns.append(k)
                    seen.add(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row if isinstance(row, dict) else {"value": row})
    return buf.getvalue().rstrip("\r\n")


def _to_md(data: Any) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        if not data:
            return "_（无数据）_"
        columns: list[str] = []
        seen: set[str] = set()
        for row in data:
            if isinstance(row, dict):
                for k in row:
                    if k not in seen:
                        columns.append(k)
                        seen.add(k)
        if not columns:
            return str(data)
        header = "| " + " | ".join(columns) + " |"
        sep = "| " + " | ".join("---" for _ in columns) + " |"
        lines = [header, sep]
        for row in data:
            vals = [str(row.get(c, "")) for c in columns]
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)
    if isinstance(data, dict):
        lines = ["| 字段 | 值 |", "| --- | --- |"]
        for k, v in data.items():
            lines.append(f"| {k} | {v} |")
        return "\n".join(lines)
    return str(data)


def _to_xlsx(data: Any) -> bytes:
    import pandas as pd  # 延迟导入，仅在 xlsx 格式时加载

    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = [data]
    else:
        rows = [{"value": data}]
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


def _write_file(data: Any, path: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(str(data), encoding="utf-8")
    return str(p)
