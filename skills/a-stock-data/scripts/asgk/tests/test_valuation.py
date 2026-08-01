"""组合估值接口测试（离线）。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from asgk.valuation import full_valuation


def _tencent_response() -> MagicMock:
    values = [""] * 50
    values[1] = "贵州茅台"
    values[3] = "1350.60"
    values[39] = "20.41"
    values[44] = "16883.60"
    values[46] = "7.25"
    response = MagicMock()
    response.read.return_value = ('v_sh600519="' + "~".join(values) + '";').encode("gbk")
    return response


def test_full_valuation_accepts_list_records_forecast():
    forecast = [
        {"年度": 2026, "预测机构数": 46, "均值": 68.7},
        {"年度": 2027, "预测机构数": 40, "均值": 73.96},
    ]
    with patch("urllib.request.urlopen", return_value=_tencent_response()), \
         patch("asgk.reports.ths_eps_forecast", return_value=forecast):
        result = full_valuation("600519")
    assert result["eps_cur"] == 68.7
    assert result["eps_next"] == 73.96
    assert result["pe_fwd"] == 19.7
    assert result["analyst_count"] == 46
    assert result["peg"] is not None
