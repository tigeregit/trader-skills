"""asgk._datacenter 全量分页单元测试。

验证 datacenter() 的分页能力（all_pages/max_pages）与默认行为兼容性。
mock em_get，不打真实东财。

覆盖分页、空响应、字段映射和请求参数等关键行为。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from asgk._datacenter import datacenter


def _resp(data: list, pages: int = 1) -> MagicMock:
    """构造 datacenter 响应 mock。"""
    r = MagicMock()
    r.json.return_value = {"result": {"data": data, "pages": pages}}
    return r


def _resp_empty() -> MagicMock:
    """result 为空的响应（无数据）。"""
    r = MagicMock()
    r.json.return_value = {"result": None}
    return r


class TestDefaultBehavior:
    def test_default_first_page_only(self):
        """默认 all_pages=False 只取第一页（向后兼容）。"""
        with patch("asgk._datacenter.em_get",
                   return_value=_resp([{"a": 1}], pages=3)) as m:
            result = datacenter("RPT_TEST")
        assert result == [{"a": 1}]
        assert m.call_count == 1  # 只调一次

    def test_empty_result_returns_empty_list(self):
        with patch("asgk._datacenter.em_get", return_value=_resp_empty()):
            assert datacenter("RPT_TEST") == []

    def test_data_null_returns_empty(self):
        """result 存在但 data 为 null。"""
        r = MagicMock()
        r.json.return_value = {"result": {"data": None, "pages": 0}}
        with patch("asgk._datacenter.em_get", return_value=r):
            assert datacenter("RPT_TEST") == []


class TestAllPages:
    def test_single_page_all_pages(self):
        """all_pages=True 但只有 1 页，应只调一次。"""
        with patch("asgk._datacenter.em_get",
                   return_value=_resp([{"a": 1}], pages=1)) as m:
            result = datacenter("RPT_TEST", all_pages=True)
        assert result == [{"a": 1}]
        assert m.call_count == 1

    def test_multi_page_aggregation(self):
        """多页：遍历所有页并聚合 data。"""
        responses = [
            _resp([{"p": 1}], pages=3),
            _resp([{"p": 2}], pages=3),
            _resp([{"p": 3}], pages=3),
        ]
        with patch("asgk._datacenter.em_get", side_effect=responses) as m:
            result = datacenter("RPT_TEST", all_pages=True)
        assert result == [{"p": 1}, {"p": 2}, {"p": 3}]
        assert m.call_count == 3

    def test_page_number_increments(self):
        """分页请求的 pageNumber 参数应递增（1,2,3）。"""
        responses = [_resp([{"p": i}], pages=3) for i in range(1, 4)]
        with patch("asgk._datacenter.em_get", side_effect=responses) as m:
            datacenter("RPT_TEST", all_pages=True)
        page_numbers = [
            call.kwargs["params"]["pageNumber"]
            for call in m.call_args_list
        ]
        assert page_numbers == ["1", "2", "3"]

    def test_max_pages_truncation(self):
        """max_pages 截断：3 页但 max_pages=2，只取前 2 页。"""
        responses = [
            _resp([{"p": 1}], pages=3),
            _resp([{"p": 2}], pages=3),
        ]
        with patch("asgk._datacenter.em_get", side_effect=responses) as m:
            result = datacenter("RPT_TEST", all_pages=True, max_pages=2)
        assert result == [{"p": 1}, {"p": 2}]
        assert m.call_count == 2

    def test_empty_first_page_stops(self):
        """all_pages=True 但第一页就空，应停止遍历。"""
        with patch("asgk._datacenter.em_get", return_value=_resp_empty()) as m:
            result = datacenter("RPT_TEST", all_pages=True)
        assert result == []
        assert m.call_count == 1

    def test_pages_as_string(self):
        """pages 字段为字符串形式也能正确解析。"""
        r = MagicMock()
        r.json.return_value = {"result": {"data": [{"x": 1}], "pages": "1"}}
        with patch("asgk._datacenter.em_get", return_value=r):
            result = datacenter("RPT_TEST", all_pages=True)
        assert result == [{"x": 1}]

    def test_large_page_size_passed(self):
        """全市场扫描场景的大 page_size 正确传递。"""
        with patch("asgk._datacenter.em_get",
                   return_value=_resp([{"a": 1}], pages=1)) as m:
            datacenter("RPT_TEST", all_pages=True, page_size=5000)
        assert m.call_args.kwargs["params"]["pageSize"] == "5000"


class TestSourceParam:
    def test_default_source_web(self):
        """默认 source=WEB（datacenter-web 端点）。"""
        with patch("asgk._datacenter.em_get",
                   return_value=_resp([{"a": 1}], pages=1)) as m:
            datacenter("RPT_TEST")
        assert m.call_args.kwargs["params"]["source"] == "WEB"

    def test_custom_source(self):
        """可自定义 source（如 securities 端点用 HSF10）。"""
        with patch("asgk._datacenter.em_get",
                   return_value=_resp([{"a": 1}], pages=1)) as m:
            datacenter("RPT_TEST", source="HSF10")
        assert m.call_args.kwargs["params"]["source"] == "HSF10"


class TestExtraParams:
    def test_extra_params_merged(self):
        """extra_params 合并到请求参数（如商誉的固定 token）。"""
        with patch("asgk._datacenter.em_get",
                   return_value=_resp([{"a": 1}], pages=1)) as m:
            datacenter("RPT_TEST", extra_params={"token": "abc123"})
        p = m.call_args.kwargs["params"]
        assert p["token"] == "abc123"
        # 基础参数仍在
        assert p["reportName"] == "RPT_TEST"

    def test_no_extra_params_backward_compat(self):
        """不传 extra_params 时行为不变（向后兼容）。"""
        with patch("asgk._datacenter.em_get",
                   return_value=_resp([{"a": 1}], pages=1)) as m:
            datacenter("RPT_TEST")
        assert "token" not in m.call_args.kwargs["params"]

    def test_extra_params_all_pages(self):
        """all_pages 模式下 extra_params 也透传到每一页。"""
        responses = [
            _resp([{"p": 1}], pages=2),
            _resp([{"p": 2}], pages=2),
        ]
        with patch("asgk._datacenter.em_get", side_effect=responses) as m:
            datacenter("RPT_TEST", all_pages=True, extra_params={"token": "x"})
        # 两页都应带 token
        for call in m.call_args_list:
            assert call.kwargs["params"]["token"] == "x"
