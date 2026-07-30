"""asgk._xlsx — xlsx 二进制流解析（深交所 ShowReport 系列用）。

HTTP API 返回的 xlsx bytes → list[dict]。
依赖 openpyxl（直接声明，非 pandas 传递依赖）。
"""
from __future__ import annotations

from io import BytesIO

import pandas as pd


def parse_xlsx(content: bytes, dtype: dict | None = None) -> list[dict]:
    """HTTP 响应的 xlsx bytes → list[dict]。

    Args:
        content: HTTP 响应的 xlsx 二进制内容
        dtype: 列类型映射，如 {"证券代码": str}（保前导零）
    Returns:
        record 列表（每行一个 dict）
    """
    df = pd.read_excel(BytesIO(content), engine="openpyxl", dtype=dtype)
    return df.to_dict("records")
