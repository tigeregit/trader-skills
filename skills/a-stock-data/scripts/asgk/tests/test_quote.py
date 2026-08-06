"""百度 K 线经网关取数与错误诊断回归测试 + 腾讯行情能力代理路由测试。

百度源经网关(baidu 组，网关用 curl_cffi 指纹出网)，asgk 端只调 em_get。
指纹由网关负责，本测试验证 em_get 调用参数与 _parse_baidu_kline 错误诊断。

tencent_quote 路由（§3.4 渐进迁移）：优先走能力代理服务端，回退旧 em_get 网关路径。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from asgk import em_proxy
from asgk.quote import baidu_kline_with_ma, mootdx_bars, tencent_quote


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


# ── tencent_quote 路由（§3.4 渐进迁移）────────────────────────
class TestTencentQuoteRouting:
    """tencent_quote 优先走能力代理服务端，回退旧 em_get 网关路径。"""

    def test_server_path_used_when_configured(self, monkeypatch):
        """配了 ASGK_SERVER → 走服务端语义接口，返回服务端结构化数据。"""
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        server_data = {"600519": {"name": "贵州茅台", "price": 1820.0, "pe_ttm": 30.5}}
        with patch("asgk.quote._server_call", return_value=server_data) as sc, \
             patch("asgk.quote.em_get") as gw:
            result = tencent_quote(["600519"])
        assert result == server_data
        sc.assert_called_once_with("quote", {"codes": ["600519"]})
        gw.assert_not_called()  # 服务端命中，不走旧网关

    def test_fallback_to_gateway_when_server_unset(self, monkeypatch):
        """未配 ASGK_SERVER → _server_call 返回 None → 回退旧 em_get 路径。"""
        monkeypatch.setattr(em_proxy, "_SERVER", None)
        resp = MagicMock()
        resp.content = "".encode("gbk")  # 空响应
        with patch("asgk.quote.em_get", return_value=resp) as gw:
            result = tencent_quote(["600519"])
        assert result == {}  # 空 GBK → 空 dict
        gw.assert_called_once()  # 走了旧网关路径

    def test_fallback_to_gateway_when_server_fails(self, monkeypatch):
        """配了服务端但调用失败（不可达/报错）→ 回退旧路径。"""
        monkeypatch.setattr(em_proxy, "_SERVER", "http://srv:7701")
        resp = MagicMock()
        resp.content = "".encode("gbk")
        with patch("asgk.quote._server_call", return_value=None) as sc, \
             patch("asgk.quote.em_get", return_value=resp) as gw:
            tencent_quote(["600519"])
        sc.assert_called_once()  # 试了服务端
        gw.assert_called_once()  # 失败后回退旧网关

    def test_legacy_path_parses_gbk_53_fields(self, monkeypatch):
        """回退路径：GBK 53 字段映射正确（与旧实现一致）。"""
        monkeypatch.setattr(em_proxy, "_SERVER", None)
        # 构造 53 字段的腾讯行（price=1820.50, pe_ttm=30.5）
        fields = ["0"] * 53
        fields[1] = "贵州茅台"
        fields[3] = "1820.50"
        fields[39] = "30.5"
        fields[46] = "10.5"
        line = f'v_sh600519="{"~".join(fields)}";'
        resp = MagicMock()
        resp.content = line.encode("gbk")
        with patch("asgk.quote.em_get", return_value=resp):
            result = tencent_quote(["600519"])
        assert "600519" in result
        assert result["600519"]["name"] == "贵州茅台"
        assert result["600519"]["price"] == 1820.50
        assert result["600519"]["pe_ttm"] == 30.5
        assert result["600519"]["pb"] == 10.5
