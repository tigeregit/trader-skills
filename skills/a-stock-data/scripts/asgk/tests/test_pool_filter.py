"""asgk.pool_filter 单元测试。

验证股权质押/商誉的字段映射、filter 转换、token 透传、all_pages。
mock _datacenter，不打外网。字段名基于真机确认（2026-07-31）。
"""
from __future__ import annotations

from unittest.mock import patch

from asgk.pool_filter import pledge_ratio, goodwill, _GOODWILL_TOKEN


# 股权质押真机样本（RPT_CSDC_LIST）
_PLEDGE_RAW = {
    "SECURITY_CODE": "000001", "SECURITY_NAME_ABBR": "平安银行",
    "INDUSTRY": "银行Ⅱ", "TRADE_DATE": "2024-09-06 00:00:00",
    "PLEDGE_RATIO": 0.02, "PLEDGE_DEAL_NUM": 4,
    "PLEDGE_MARKET_CAP": 3365.5104,
    "REPURCHASE_BALANCE": 333.88, "REPURCHASE_UNLIMITED_BALANCE": 333.88,
}

# 商誉真机样本（RPT_GOODWILL_STOCKDETAILS）
_GOODWILL_RAW = {
    "SECURITY_CODE": "000001", "SECURITY_NAME_ABBR": "平安银行",
    "INDUSTRY_CFT": "银行Ⅱ",
    "REPORT_DATE": "2023-12-31 00:00:00", "NOTICE_DATE": "2025-03-15 00:00:00",
    "GOODWILL": 7568000000, "SUMSHEQUITY_RATIO": 0.016022763842,
    "PARENTNETPROFIT": 46455000000, "PNP_YOY_RATIO": 0.020630108094,
}


class TestPledgeRatio:
    def test_filter_uses_iso_date(self):
        with patch("asgk.pool_filter._datacenter", return_value=[]) as m:
            pledge_ratio("20240906")
        assert "(TRADE_DATE='2024-09-06')" in m.call_args.kwargs["filter_str"]

    def test_all_pages_true(self):
        with patch("asgk.pool_filter._datacenter", return_value=[]) as m:
            pledge_ratio("20240906")
        assert m.call_args.kwargs["all_pages"] is True

    def test_report_name(self):
        with patch("asgk.pool_filter._datacenter", return_value=[]) as m:
            pledge_ratio("20240906")
        assert m.call_args.args[0] == "RPT_CSDC_LIST"

    def test_field_mapping(self):
        with patch("asgk.pool_filter._datacenter", return_value=[_PLEDGE_RAW]):
            result = pledge_ratio("20240906")
        r = result[0]
        assert r["code"] == "000001"
        assert r["name"] == "平安银行"
        assert r["industry"] == "银行Ⅱ"
        assert r["trade_date"] == "2024-09-06"
        assert r["pledge_ratio"] == 0.02
        assert r["pledge_deal_num"] == 4
        assert r["pledge_market_cap"] == 3365.5104
        assert r["repurchase_balance"] == 333.88

    def test_empty_returns_empty_list(self):
        with patch("asgk.pool_filter._datacenter", return_value=[]):
            assert pledge_ratio("20240906") == []


class TestGoodwill:
    def test_filter_uses_iso_date(self):
        with patch("asgk.pool_filter._datacenter", return_value=[]) as m:
            goodwill("20231231")
        assert "(REPORT_DATE='2023-12-31')" in m.call_args.kwargs["filter_str"]

    def test_all_pages_true(self):
        with patch("asgk.pool_filter._datacenter", return_value=[]) as m:
            goodwill("20231231")
        assert m.call_args.kwargs["all_pages"] is True

    def test_report_name(self):
        with patch("asgk.pool_filter._datacenter", return_value=[]) as m:
            goodwill("20231231")
        assert m.call_args.args[0] == "RPT_GOODWILL_STOCKDETAILS"

    def test_token_passed(self):
        """商誉接口的固定 token 必须透传到 _datacenter。"""
        with patch("asgk.pool_filter._datacenter", return_value=[]) as m:
            goodwill("20231231")
        assert m.call_args.kwargs["extra_params"] == {"token": _GOODWILL_TOKEN}

    def test_field_mapping(self):
        with patch("asgk.pool_filter._datacenter", return_value=[_GOODWILL_RAW]):
            result = goodwill("20231231")
        r = result[0]
        assert r["code"] == "000001"
        assert r["name"] == "平安银行"
        assert r["industry"] == "银行Ⅱ"
        assert r["report_date"] == "2023-12-31"
        assert r["notice_date"] == "2025-03-15"
        assert r["goodwill"] == 7568000000
        assert r["goodwill_to_equity"] == 0.016022763842
        assert r["net_profit"] == 46455000000
        assert r["net_profit_yoy"] == 0.020630108094

    def test_empty_returns_empty_list(self):
        with patch("asgk.pool_filter._datacenter", return_value=[]):
            assert goodwill("20231231") == []
