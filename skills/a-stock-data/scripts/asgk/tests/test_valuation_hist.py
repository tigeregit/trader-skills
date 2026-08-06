"""asgk.valuation_hist 单元测试。

验证乐咕 PE/PB 的 token 生成、CSRF 解析、请求参数、字段映射。
mock em_get，不打外网（经网关，未配 ASGK_GW 会 fail-closed，故必须 mock）。
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock, call

from asgk.valuation_hist import (
    market_pe_lg, market_pb_lg, _legu_token, _legu_csrf_from,
)


def _mock_csrf_response() -> MagicMock:
    """构造乐咕页面响应（含 CSRF meta + cookie）。"""
    r = MagicMock()
    r.text = '<html><head><meta name="_csrf" content="abc-123"></head></html>'
    r.cookies = {"session": "xyz"}
    return r


def _mock_api_response(data: list) -> MagicMock:
    r = MagicMock()
    r.json.return_value = {"data": data}
    return r


class TestLeguToken:
    def test_token_is_md5_of_today(self):
        """token = md5(当日日期 ISO)（与 akshare JS 版一致）。"""
        from datetime import datetime
        from hashlib import md5
        today = datetime.now().date().isoformat()
        assert _legu_token() == md5(today.encode()).hexdigest()

    def test_token_is_hex_string(self):
        assert len(_legu_token()) == 32  # md5 hexdigest 长度


class TestLeguCsrfFrom:
    def test_extracts_csrf_and_cookies(self):
        """_legu_csrf_from 从页面响应解析 CSRF token + cookie。"""
        headers, cookie_str = _legu_csrf_from(_mock_csrf_response())
        assert headers["X-CSRF-Token"] == "abc-123"
        assert "User-Agent" in headers
        assert "session=xyz" in cookie_str

    def test_returns_empty_cookie_when_none(self):
        r = MagicMock()
        r.text = '<html><meta name="_csrf" content="t"></html>'
        r.cookies = {}
        _, cookie_str = _legu_csrf_from(r)
        assert cookie_str == ""

    def test_raises_if_no_csrf(self):
        r = MagicMock()
        r.text = "<html></html>"
        try:
            _legu_csrf_from(r)
            assert False, "应抛 ValueError"
        except ValueError:
            pass


class TestMarketPeLg:
    def test_valid_market(self):
        # em_get 被调两次：第一次页面(CSRF)，第二次 API
        with patch("asgk.valuation_hist.em_get",
                   side_effect=[_mock_csrf_response(),
                                _mock_api_response([{"date": "2024-01-01", "close": 3000, "pe": 15.5}])]):
            result = market_pe_lg("上证")
        assert len(result) == 1
        assert result[0]["date"] == "2024-01-01"
        assert result[0]["close"] == 3000
        assert result[0]["pe"] == 15.5

    def test_invalid_market_raises(self):
        try:
            market_pe_lg("科创版")  # PE 暂不支持科创版
            assert False, "应抛 ValueError"
        except ValueError:
            pass

    def test_uses_market_id_param(self):
        """PE 用 marketId（小写d）参数，且第二次 em_get 带它。"""
        with patch("asgk.valuation_hist.em_get",
                   side_effect=[_mock_csrf_response(), _mock_api_response([])]) as m:
            market_pe_lg("深证")
        # 第二次调用（API）的 params 含 marketId
        api_call = m.call_args_list[1]
        assert api_call.kwargs["params"]["marketId"] == "2"

    def test_token_passed(self):
        with patch("asgk.valuation_hist.em_get",
                   side_effect=[_mock_csrf_response(), _mock_api_response([])]) as m:
            market_pe_lg("上证")
        api_call = m.call_args_list[1]
        assert "token" in api_call.kwargs["params"]


class TestMarketPbLg:
    def test_valid_market(self):
        with patch("asgk.valuation_hist.em_get",
                   side_effect=[_mock_csrf_response(),
                                _mock_api_response([{"date": "2024-01-01", "close": 3000, "pb": 2.1, "addPb": 0.5}])]):
            result = market_pb_lg("上证")
        assert result[0]["pb"] == 2.1
        assert result[0]["add_pb"] == 0.5

    def test_supports_kechuang(self):
        """PB 支持科创版（indexCode=7）。"""
        with patch("asgk.valuation_hist.em_get",
                   side_effect=[_mock_csrf_response(), _mock_api_response([])]) as m:
            market_pb_lg("科创版")
        api_call = m.call_args_list[1]
        assert api_call.kwargs["params"]["indexCode"] == "7"

    def test_uses_index_code_param(self):
        """PB 用 indexCode 参数（非 marketId）。"""
        with patch("asgk.valuation_hist.em_get",
                   side_effect=[_mock_csrf_response(), _mock_api_response([])]) as m:
            market_pb_lg("创业板")
        api_call = m.call_args_list[1]
        assert api_call.kwargs["params"]["indexCode"] == "4"

    def test_empty_returns_empty_list(self):
        with patch("asgk.valuation_hist.em_get",
                   side_effect=[_mock_csrf_response(), _mock_api_response([])]):
            assert market_pb_lg("上证") == []
