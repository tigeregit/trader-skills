"""百度 K 线经网关取数与错误诊断回归测试。

百度源经网关(baidu 组，网关用 curl_cffi 指纹出网)，asgk 端只调 em_get。
指纹由网关负责，本测试验证 em_get 调用参数与 _parse_baidu_kline 错误诊断。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from asgk.quote import baidu_kline_with_ma, mootdx_bars


def _response(payload: dict, status: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    return response


def _ok_payload() -> dict:
    return {
        "ResultCode": "0",
        "Result": {
            "newMarketData": {
                "keys": ["time", "close", "ma5avgprice"],
                "marketData": "2026-07-30,1400.00,1390.00;2026-07-31,1410.00,1395.00",
            }
        },
    }


def test_baidu_routes_through_gateway():
    """baidu_kline_with_ma 经 em_get 调用，指纹由网关负责(不在 asgk 设 impersonate)。"""
    with patch("asgk.quote.em_get", return_value=_response(_ok_payload())) as get:
        result = baidu_kline_with_ma("600519")

    assert result["keys"] == ["time", "close", "ma5avgprice"]
    assert len(result["rows"]) == 2
    assert get.call_args.args[0] == "https://finance.pae.baidu.com/selfselect/getstockquotation"
    assert get.call_args.kwargs["params"]["code"] == "600519"
    assert get.call_args.kwargs["tier"] == "R"
    # asgk 端不再传 impersonate（网关负责指纹）
    assert "impersonate" not in get.call_args.kwargs


def test_baidu_distinguishes_http_200_business_403():
    denied = {"ResultCode": "403", "Result": []}
    with patch("asgk.quote.em_get", return_value=_response(denied, 200)):
        with pytest.raises(RuntimeError, match=r"HTTP 200.*ResultCode=403"):
            baidu_kline_with_ma("600519")


def test_baidu_distinguishes_http_error_from_business_code():
    denied = {"ResultCode": "403", "Result": []}
    with patch("asgk.quote.em_get", return_value=_response(denied, 403)):
        with pytest.raises(RuntimeError, match=r"HTTP 403.*ResultCode=403"):
            baidu_kline_with_ma("600519")


def test_baidu_non_json_response_raises():
    """网关返回非 JSON（如错误页）时给出清晰错误，不泄漏原始内容。"""
    response = MagicMock()
    response.status_code = 502
    response.json.side_effect = ValueError("not json")
    with patch("asgk.quote.em_get", return_value=response):
        with pytest.raises(RuntimeError, match=r"非 JSON 响应"):
            baidu_kline_with_ma("600519")


def test_mootdx_daily_bars_fall_back_to_baidu_when_empty():
    client = MagicMock()
    client.bars.return_value = None
    payload = {
        "keys": ["time", "open", "close", "volume", "high", "low", "amount"],
        "rows": ["2026-07-31,10.0,10.2,1000,10.3,9.9,10200"],
    }
    with patch("asgk.quote.tdx_client", return_value=client), \
         patch("asgk.quote.baidu_kline_with_ma", return_value=payload):
        result = mootdx_bars("600519", frequency=9, offset=10)
    assert result == [{"open": 10.0, "close": 10.2, "high": 10.3, "low": 9.9,
                       "vol": 1000.0, "amount": 10200.0, "datetime": "2026-07-31"}]


def test_mootdx_intraday_empty_does_not_use_daily_fallback():
    client = MagicMock()
    client.bars.return_value = None
    with patch("asgk.quote.tdx_client", return_value=client), \
         patch("asgk.quote.baidu_kline_with_ma") as baidu:
        assert mootdx_bars("600519", frequency=0, offset=10) == []
    baidu.assert_not_called()
