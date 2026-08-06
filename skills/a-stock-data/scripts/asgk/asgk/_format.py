"""asgk._format — 客户端格式化层（§3.5）。

按数据类型（kv/table/series/text）把结构化数据格式化为 json/csv/md/xlsx/plain。
格式化是纯客户端计算：无网络、无状态、在服务端缓存之后——同一份数据只缓存一份，
多 agent 各自按需渲染，格式不进 cache key。

数据类型 → 支持格式矩阵（§3.5）：
  table（list[dict]）：json/csv/md/xlsx
  kv（dict）：          json/md          （csv 单行无意义、xlsx 不适用）
  series（K线/资金流）： json/csv/md/xlsx
  text（F10/研报正文）： json/md/plain    （csv、xlsx 不适用）
  document（PDF/年报）： 不走格式化层（§3.7，原始 bytes 直交付）

不支持的组合在客户端就报错（不打扰服务端）。
"""
from __future__ import annotations

import csv
import io
import json as _json
from typing import Any

# 数据类型 → 支持格式（§3.5 矩阵）
_SUPPORTED: dict[str, set[str]] = {
    "table": {"json", "csv", "md", "xlsx"},
    "kv": {"json", "md"},
    "series": {"json", "csv", "md", "xlsx"},
    "text": {"json", "md", "plain"},
    "document": set(),  # 文档型不走格式化层
}


def supported_formats(data_type: str) -> set[str]:
    """该数据类型支持的格式集合。document 型返回空集（不走格式化层）。"""
    return _SUPPORTED.get(data_type, set())


def validate(data_type: str, fmt: str) -> None:
    """校验 (data_type, fmt) 组合合法，否则 ValueError。

    在客户端请求前校验，不支持的组合在此报错，不打扰服务端。
    """
    allowed = _SUPPORTED.get(data_type)
    if allowed is None:
        raise ValueError(f"未知数据类型: {data_type!r}")
    if data_type == "document":
        raise ValueError(f"文档型不走格式化层（原始 bytes 直交付，见 §3.7）")
    if fmt not in allowed:
        raise ValueError(
            f"{data_type}型不支持 {fmt!r}，支持: {sorted(allowed)}"
        )


def format_data(data: Any, data_type: str, fmt: str) -> str | bytes:
    """把结构化数据格式化为指定格式。

    Args:
        data: 业务函数返回的结构化数据（dict/list/str）
        data_type: 数据类型（kv/table/series/text）
        fmt: 目标格式（json/csv/md/xlsx/plain）
    Returns:
        str（json/csv/md/plain）或 bytes（xlsx）
    Raises:
        ValueError: 不支持的 (data_type, fmt) 组合
    """
    validate(data_type, fmt)
    if fmt == "json":
        return _to_json(data)
    if fmt == "csv":
        return _to_csv(data, data_type)
    if fmt == "md":
        return _to_md(data, data_type)
    if fmt == "xlsx":
        return _to_xlsx(data, data_type)
    if fmt == "plain":
        return data if isinstance(data, str) else str(data)
    raise ValueError(f"未知格式: {fmt!r}")


# ── 各格式实现 ────────────────────────────────────────────────
def _to_json(data: Any) -> str:
    """JSON 格式化（ensure_ascii=False 保留中文，indent=2 可读）。"""
    return _json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _to_csv(data: Any, data_type: str) -> str:
    """CSV 格式化（table/series：list[dict] 取并集列）。"""
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        # kv 型不应到这（validate 已挡），兜底：单行
        rows = [data]
    else:
        rows = [{"value": data}]
    if not rows:
        return ""
    # 并集列（保序：按首次出现的顺序）
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


def _to_md(data: Any, data_type: str) -> str:
    """Markdown 格式化（table/series：表格；kv：键值列表；text：原样）。"""
    if isinstance(data, str):
        return data  # text 型原样
    if isinstance(data, list):
        if not data:
            return "_（无数据）_"
        # 取并集列
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
        # kv：键值列表
        lines = [f"| 字段 | 值 |", "| --- | --- |"]
        for k, v in data.items():
            lines.append(f"| {k} | {v} |")
        return "\n".join(lines)
    return str(data)


def _to_xlsx(data: Any, data_type: str) -> bytes:
    """xlsx 格式化（table/series：list[dict] → DataFrame → xlsx bytes）。"""
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
