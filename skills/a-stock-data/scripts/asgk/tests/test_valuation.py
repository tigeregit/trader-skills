"""组合估值接口测试（离线）。"""
from __future__ import annotations

from unittest.mock import patch

from asgk.valuation import full_valuation


def _tencent_quote_result() -> dict:
    """模拟 tencent_quote 返回的单票行情（经网关后的结构化结果）。"""
    return {"600519": {"name": "贵州茅台", "price": 1350.60, "pe_ttm": 20.41,
                       "mcap_yi": 16883.60, "pb": 7.25}}


def test_full_valuation_accepts_list_records_forecast():
    forecast = [
        {"年度": 2026, "预测机构数": 46, "均值": 68.7},
        {"年度": 2027, "预测机构数": 40, "均值": 73.96},
    ]
    # full_valuation 经网关调 tencent_quote，mock 它而非底层 urlopen
    with patch("asgk.valuation.tencent_quote", return_value=_tencent_quote_result()), \
         patch("asgk.reports.ths_eps_forecast", return_value=forecast):
        result = full_valuation("600519")
    assert result["eps_cur"] == 68.7
    assert result["eps_next"] == 73.96
    assert result["pe_fwd"] == 19.7
    assert result["analyst_count"] == 46
    assert result["peg"] is not None
