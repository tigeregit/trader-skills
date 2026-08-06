"""datacenter 能力服务端测试（mock 东财 datacenter-web 上游）。

验证东财数据中心查询机制正确下沉到服务端：
  - 单页查询：参数构造（reportName/filter/sort/source/client）与 asgk 一致
  - 全量分页：all_pages=True 遍历 result.pages 聚合
  - 空响应 / data null / 缺 pages 的容错
  - extra_params（商誉 token）透传
  - 403 触发熔断

不打真实东财——mock asgk_server.capabilities.datacenter.egress_request。
与 asgk/tests/test_datacenter.py 的契约对齐（分页/空/参数行为一致）。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from asgk_server import registry
from asgk_server.server import CapabilityServer


@pytest.fixture(autouse=True)
def _keep_registry():
    """datacenter 能力在模块导入时注册，不要 clear。"""
    yield


def _config() -> dict:
    return {
        "group": [{"name": "eastmoney", "domains": ["datacenter-web.eastmoney.com"],
                   "rps": 100, "jitter": [0, 0]}],
        "circuit": {"cooldown_seconds": 300, "failure_threshold": 3,
                    "probe_lease_seconds": 120},
        "state": {"enabled": False},
        "retry": {"max_attempts": 1},
        "cache": {"session": {"intraday_start": "09:00", "intraday_end": "18:00"},
                  "persist": {"enabled": False}},
        "fingerprint": {"enabled": False},
    }


@pytest.fixture
def srv() -> CapabilityServer:
    s = CapabilityServer(_config())
    yield s
    s.close()


def _resp(data: list, pages: int = 1, status: int = 200) -> MagicMock:
    """构造 datacenter 响应 mock。"""
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"result": {"data": data, "pages": pages}}
    return r


def _resp_empty(status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"result": None}
    return r


# ── 单页查询 ──────────────────────────────────────────────────
class TestSinglePage:
    def test_default_first_page_only(self, srv):
        """all_pages 默认 False，只取第一页。"""
        with patch("asgk_server.capabilities.datacenter.egress_request",
                   return_value=_resp([{"SECURITY_CODE": "600519"}], pages=3)) as m:
            status, payload = srv.handle_capability(
                "datacenter", {"report_name": "RPT_TEST"})
        assert status == 200
        assert payload["data"] == [{"SECURITY_CODE": "600519"}]
        assert m.call_count == 1  # 只调一次

    def test_request_params_constructed(self, srv):
        """参数构造与 asgk 一致：reportName/columns=ALL/filter/pageNumber/..."""
        with patch("asgk_server.capabilities.datacenter.egress_request",
                   return_value=_resp([])) as m:
            srv.handle_capability("datacenter", {
                "report_name": "RPT_FOO", "filter_str": '(SCODE="600519")',
                "page_size": 30, "sort_columns": "DATE", "sort_types": "-1",
            })
        _method, _client, url = m.call_args.args
        kwargs = m.call_args.kwargs
        assert url == "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = kwargs["params"]
        assert params["reportName"] == "RPT_FOO"
        assert params["columns"] == "ALL"
        assert params["filter"] == '(SCODE="600519")'
        assert params["pageNumber"] == "1"
        assert params["pageSize"] == "30"
        assert params["sortColumns"] == "DATE"
        assert params["sortTypes"] == "-1"
        assert params["source"] == "WEB"
        assert params["client"] == "WEB"

    def test_source_param_overridable(self, srv):
        """dc_source 参数可覆盖东财端点 source（如 securities 端点用 HSF10）。

        注：能力的选源控制参数叫 source（指数据源 tencent/eastmoney），东财端点的
        source 字段（WEB/HSF10）改名 dc_source 避开冲突。
        """
        with patch("asgk_server.capabilities.datacenter.egress_request",
                   return_value=_resp([])) as m:
            srv.handle_capability("datacenter", {
                "report_name": "RPT_FOO", "dc_source": "HSF10",
            })
        assert m.call_args.kwargs["params"]["source"] == "HSF10"

    def test_extra_params_passed(self, srv):
        """extra_params（如商誉 token）透传到查询参数。"""
        with patch("asgk_server.capabilities.datacenter.egress_request",
                   return_value=_resp([])) as m:
            srv.handle_capability("datacenter", {
                "report_name": "RPT_GOODWILL",
                "extra_params": {"token": "abc123"},
            })
        assert m.call_args.kwargs["params"]["token"] == "abc123"


# ── 全量分页 ──────────────────────────────────────────────────
class TestAllPages:
    def test_paginates_all_pages(self, srv):
        """all_pages=True 遍历所有页，聚合 data。"""
        responses = [
            _resp([{"i": 1}, {"i": 2}], pages=3),
            _resp([{"i": 3}], pages=3),
            _resp([{"i": 4}], pages=3),
        ]
        with patch("asgk_server.capabilities.datacenter.egress_request",
                   side_effect=responses) as m:
            status, payload = srv.handle_capability("datacenter", {
                "report_name": "RPT_TEST", "all_pages": True, "page_size": 2,
            })
        assert status == 200
        assert [r["i"] for r in payload["data"]] == [1, 2, 3, 4]
        assert m.call_count == 3

    def test_max_pages_caps_iteration(self, srv):
        """max_pages 限制最大页数，防失控。"""
        responses = [_resp([{"i": i}], pages=100) for i in range(5)]
        with patch("asgk_server.capabilities.datacenter.egress_request",
                   side_effect=responses) as m:
            srv.handle_capability("datacenter", {
                "report_name": "RPT_TEST", "all_pages": True, "max_pages": 3,
            })
        assert m.call_count == 3

    def test_stops_when_no_pages_meta(self, srv):
        """result 缺 pages 元信息 → 只取第一页。"""
        with patch("asgk_server.capabilities.datacenter.egress_request",
                   return_value=_resp([{"a": 1}], pages=0)) as m:
            status, payload = srv.handle_capability("datacenter", {
                "report_name": "RPT_TEST", "all_pages": True,
            })
        assert payload["data"] == [{"a": 1}]
        assert m.call_count == 1


# ── 容错 ──────────────────────────────────────────────────────
class TestEdgeCases:
    def test_empty_result_returns_empty(self, srv):
        with patch("asgk_server.capabilities.datacenter.egress_request",
                   return_value=_resp_empty()):
            status, payload = srv.handle_capability(
                "datacenter", {"report_name": "RPT_TEST"})
        assert status == 200
        assert payload["data"] == []

    def test_data_null_returns_empty(self, srv):
        """result 存在但 data 为 null。"""
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {"result": {"data": None, "pages": 0}}
        with patch("asgk_server.capabilities.datacenter.egress_request",
                   return_value=r):
            status, payload = srv.handle_capability(
                "datacenter", {"report_name": "RPT_TEST"})
        assert payload["data"] == []


# ── 熔断 ──────────────────────────────────────────────────────
class TestCircuit:
    def test_403_triggers_circuit(self, srv):
        with patch("asgk_server.capabilities.datacenter.egress_request",
                   return_value=_resp_empty(status=403)):
            status1, _ = srv.handle_capability(
                "datacenter", {"report_name": "RPT_TEST"})
        assert status1 == 403
        # 熔断已开，再次请求被拦
        with patch("asgk_server.capabilities.datacenter.egress_request") as m2:
            status2, _ = srv.handle_capability(
                "datacenter", {"report_name": "RPT_TEST"})
        assert status2 == 503
        m2.assert_not_called()
