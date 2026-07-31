"""asgk.holders 单元测试。

验证十大股东/流通股东（emweb）、股东变化/协同（datacenter）的字段映射、
filter 转换、参数传递。mock em_get/_datacenter，不打外网。
字段名基于真机确认（2026-07-31）。
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from asgk.holders import (
    top10_holders, top10_free_holders, holder_change, holder_teamwork,
)


def _emweb_resp(data: list, key: str = "sdgd") -> MagicMock:
    r = MagicMock()
    r.json.return_value = {key: data}
    return r


# emweb 十大股东样本（PageSDGD）
_TOP10_RAW = {
    "HOLDER_RANK": 1, "HOLDER_NAME": "中国贵州茅台酒厂(集团)有限责任公司",
    "SHARES_TYPE": "流通A股", "HOLD_NUM": 679211576,
    "HOLD_NUM_RATIO": 54.07, "HOLD_NUM_CHANGE": "不变", "CHANGE_RATIO": None,
}
# emweb 十大流通股东样本（PageSDLTGD）
_FREE_RAW = {
    "HOLDER_RANK": 1, "HOLDER_NAME": "中国贵州茅台酒厂(集团)有限责任公司",
    "HOLDER_TYPE": "其它", "SHARES_TYPE": "A股", "HOLD_NUM": 679211576,
    "FREE_HOLDNUM_RATIO": 54.068839795771, "HOLD_NUM_CHANGE": "不变", "CHANGE_RATIO": None,
}
# datacenter 股东持股变化样本（RPT_HOLDERS_BASIC_INFO）
_CHANGE_RAW = {
    "HOLDER_NAME": "全国社保基金四一三组合", "HOLDER_TYPE": "社保",
    "HOLDER_SOURCE": "十大股东", "HOLDER_NUM": 1,
    "HOLDUP_NUM": None, "HOLDDOWN_NUM": None, "HOLDADD_NUM": None,
    "HOLDUNCHANGED_NUM": 1, "HOLDER_MARKET_CAP": 79583819.32, "CLOSE_PRICE": 3336.4974,
}
# datacenter 股东协同样本（RPT_TENHOLDERS_COOPHOLDERS）
_TEAMWORK_RAW = {
    "HOLDER_NAME": "全国社保基金四一三组合", "HOLDER_TYPE": "社保",
    "COOPERAT_HOLDER_NAME": "香港中央结算有限公司", "COOPERAT_HOLDER_TYPE": "其他",
    "COOPERAT_NUM": 82,
}


class TestTop10Holders:
    def test_symbol_uppercased(self):
        with patch("asgk.holders.em_get", return_value=_emweb_resp([])) as m:
            top10_holders("sh600519", "20240930")
        assert m.call_args.kwargs["params"]["code"] == "SH600519"

    def test_date_to_iso(self):
        with patch("asgk.holders.em_get", return_value=_emweb_resp([])) as m:
            top10_holders("sh600519", "20240930")
        assert m.call_args.kwargs["params"]["date"] == "2024-09-30"

    def test_emweb_url(self):
        with patch("asgk.holders.em_get", return_value=_emweb_resp([])) as m:
            top10_holders("sh600519", "20240930")
        assert "PageSDGD" in m.call_args.args[0]

    def test_field_mapping(self):
        with patch("asgk.holders.em_get", return_value=_emweb_resp([_TOP10_RAW])):
            result = top10_holders("sh600519", "20240930")
        r = result[0]
        assert r["rank"] == 1
        assert r["name"] == "中国贵州茅台酒厂(集团)有限责任公司"
        assert r["shares_type"] == "流通A股"
        assert r["hold_num"] == 679211576
        assert r["ratio"] == 54.07
        assert r["change"] == "不变"

    def test_empty_returns_empty_list(self):
        with patch("asgk.holders.em_get", return_value=_emweb_resp([])):
            assert top10_holders("sh600519", "20240930") == []


class TestTop10FreeHolders:
    def test_symbol_uppercased(self):
        with patch("asgk.holders.em_get", return_value=_emweb_resp([], "sdltgd")) as m:
            top10_free_holders("sh600519", "20240930")
        assert m.call_args.kwargs["params"]["code"] == "SH600519"

    def test_emweb_url(self):
        with patch("asgk.holders.em_get", return_value=_emweb_resp([], "sdltgd")) as m:
            top10_free_holders("sh600519", "20240930")
        assert "PageSDLTGD" in m.call_args.args[0]

    def test_field_mapping(self):
        with patch("asgk.holders.em_get", return_value=_emweb_resp([_FREE_RAW], "sdltgd")):
            result = top10_free_holders("sh600519", "20240930")
        r = result[0]
        assert r["rank"] == 1
        assert r["name"] == "中国贵州茅台酒厂(集团)有限责任公司"
        assert r["holder_type"] == "其它"
        assert r["shares_type"] == "A股"
        assert r["hold_num"] == 679211576
        assert r["ratio"] == 54.068839795771
        assert r["change"] == "不变"


class TestHolderChange:
    def test_filter_uses_iso_date(self):
        with patch("asgk.holders._datacenter", return_value=[]) as m:
            holder_change("20240930")
        assert "(END_DATE='2024-09-30')" in m.call_args.kwargs["filter_str"]

    def test_all_pages_true(self):
        with patch("asgk.holders._datacenter", return_value=[]) as m:
            holder_change("20240930")
        assert m.call_args.kwargs["all_pages"] is True

    def test_report_name(self):
        with patch("asgk.holders._datacenter", return_value=[]) as m:
            holder_change("20240930")
        assert m.call_args.args[0] == "RPT_HOLDERS_BASIC_INFO"

    def test_field_mapping(self):
        with patch("asgk.holders._datacenter", return_value=[_CHANGE_RAW]):
            result = holder_change("20240930")
        r = result[0]
        assert r["holder_name"] == "全国社保基金四一三组合"
        assert r["holder_type"] == "社保"
        assert r["holder_source"] == "十大股东"
        assert r["holder_num"] == 1
        assert r["holdup_num"] is None
        assert r["holdunchanged_num"] == 1
        assert r["holder_market_cap"] == 79583819.32

    def test_empty_returns_empty_list(self):
        with patch("asgk.holders._datacenter", return_value=[]):
            assert holder_change("20240930") == []


class TestHolderTeamwork:
    def test_all_pages_true(self):
        with patch("asgk.holders._datacenter", return_value=[]) as m:
            holder_teamwork("社保")
        assert m.call_args.kwargs["all_pages"] is True

    def test_report_name(self):
        with patch("asgk.holders._datacenter", return_value=[]) as m:
            holder_teamwork("社保")
        assert m.call_args.args[0] == "RPT_TENHOLDERS_COOPHOLDERS"

    def test_filter_for_specific_type(self):
        with patch("asgk.holders._datacenter", return_value=[]) as m:
            holder_teamwork("社保")
        assert m.call_args.kwargs["filter_str"] == '(HOLDER_TYPE="社保")'

    def test_no_filter_for_all(self):
        with patch("asgk.holders._datacenter", return_value=[]) as m:
            holder_teamwork("全部")
        assert m.call_args.kwargs["filter_str"] == ""

    def test_field_mapping(self):
        with patch("asgk.holders._datacenter", return_value=[_TEAMWORK_RAW]):
            result = holder_teamwork("社保")
        r = result[0]
        assert r["holder_name"] == "全国社保基金四一三组合"
        assert r["holder_type"] == "社保"
        assert r["coop_holder_name"] == "香港中央结算有限公司"
        assert r["coop_holder_type"] == "其他"
        assert r["coop_num"] == 82

    def test_empty_returns_empty_list(self):
        with patch("asgk.holders._datacenter", return_value=[]):
            assert holder_teamwork("社保") == []
