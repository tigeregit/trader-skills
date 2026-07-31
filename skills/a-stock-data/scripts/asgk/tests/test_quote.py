"""百度 K 线传输层与错误诊断回归测试。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from asgk.quote import baidu_kline_with_ma


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


def test_baidu_uses_chrome_transport_profile():
    with patch("asgk.quote.curl_requests.get", return_value=_response(_ok_payload())) as get:
        result = baidu_kline_with_ma("600519")

    assert result["keys"] == ["time", "close", "ma5avgprice"]
    assert len(result["rows"]) == 2
    assert get.call_args.args == ("https://finance.pae.baidu.com/selfselect/getstockquotation",)
    assert get.call_args.kwargs["params"]["code"] == "600519"
    assert get.call_args.kwargs["impersonate"] == "chrome"
    assert get.call_args.kwargs["timeout"] == 10


def test_baidu_distinguishes_http_200_business_403():
    denied = {"ResultCode": "403", "Result": []}
    with patch("asgk.quote.curl_requests.get", return_value=_response(denied, 200)):
        with pytest.raises(RuntimeError, match=r"HTTP 200.*ResultCode=403"):
            baidu_kline_with_ma("600519")


def test_baidu_distinguishes_http_error_from_business_code():
    denied = {"ResultCode": "403", "Result": []}
    with patch("asgk.quote.curl_requests.get", return_value=_response(denied, 403)):
        with pytest.raises(RuntimeError, match=r"HTTP 403.*ResultCode=403"):
            baidu_kline_with_ma("600519")


def test_baidu_transport_error_does_not_leak_exception_url():
    from curl_cffi.requests import RequestsError

    error = RequestsError("failed https://example.test/?cookie=secret")
    with patch("asgk.quote.curl_requests.get", side_effect=error):
        with pytest.raises(RuntimeError) as raised:
            baidu_kline_with_ma("600519")
    assert "cookie=secret" not in str(raised.value)
