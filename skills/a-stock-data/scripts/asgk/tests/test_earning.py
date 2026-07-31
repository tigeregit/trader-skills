"""asgk.earning 单元测试。

验证业绩预告/快报的字段映射、filter 转换、all_pages 传入。
mock _datacenter，不打外网。字段名基于真机确认（2026-07-31）。
"""
from __future__ import annotations

from unittest.mock import patch

from asgk.earning import earning_forecast, earning_express, _date_to_iso


# 业绩预告真机样本字段（RPT_PUBLIC_OP_NEWPREDICT）
_FCST_RAW = {
    "SECURITY_CODE": "301613", "SECURITY_NAME_ABBR": "新铝时代",
    "NOTICE_DATE": "2024-10-22 00:00:00", "REPORT_DATE": "2024-09-30 00:00:00",
    "PREDICT_FINANCE": "主营业务收入",
    "PREDICT_AMT_LOWER": 1230000000, "PREDICT_AMT_UPPER": 1330000000,
    "ADD_AMP_LOWER": 4.73, "ADD_AMP_UPPER": 13.25,
    "PREDICT_TYPE": "略增",
    "PREDICT_CONTENT": "预计2024年1-9月主营业务收入同比上年增长4.73%至13.25%",
    "PREYEAR_SAME_PERIOD": None,
}

# 业绩快报真机样本字段（RPT_FCI_PERFORMANCEE）
_EXPR_RAW = {
    "SECURITY_CODE": "300962", "SECURITY_NAME_ABBR": "中金辐照",
    "NOTICE_DATE": "2024-10-08 00:00:00", "REPORT_DATE": "2024-09-30 00:00:00",
    "BASIC_EPS": 0.3632, "PARENT_BVPS": 3.5435,
    "TOTAL_OPERATE_INCOME": 271524383.77, "TOTAL_OPERATE_INCOME_SQ": 258728525.64,
    "PARENT_NETPROFIT": 95891712.03, "PARENT_NETPROFIT_SQ": 96507053.17,
    "YSTZ": 4.945669635131, "JLRTBZCL": -0.637612609429,
    "WEIGHTAVG_ROE": 10.3,
}


class TestDateToIso:
    def test_convert(self):
        assert _date_to_iso("20240930") == "2024-09-30"
        assert _date_to_iso("20081231") == "2008-12-31"


class TestEarningForecast:
    def test_filter_uses_iso_date(self):
        """filter 必须用 YYYY-MM-DD 等值匹配（真机验证的语法）。"""
        with patch("asgk.earning._datacenter", return_value=[]) as m:
            earning_forecast("20240930")
        kwargs = m.call_args.kwargs
        assert "(REPORT_DATE='2024-09-30')" in kwargs["filter_str"]

    def test_all_pages_true(self):
        """业绩是全市场多页扫描，必须传 all_pages=True。"""
        with patch("asgk.earning._datacenter", return_value=[]) as m:
            earning_forecast("20240930")
        assert m.call_args.kwargs["all_pages"] is True

    def test_tier_L(self):
        with patch("asgk.earning._datacenter", return_value=[]) as m:
            earning_forecast("20240930")
        assert m.call_args.kwargs["tier"] == "L"

    def test_report_name(self):
        with patch("asgk.earning._datacenter", return_value=[]) as m:
            earning_forecast("20240930")
        assert m.call_args.args[0] == "RPT_PUBLIC_OP_NEWPREDICT"

    def test_field_mapping(self):
        with patch("asgk.earning._datacenter", return_value=[_FCST_RAW]):
            result = earning_forecast("20240930")
        assert len(result) == 1
        r = result[0]
        assert r["code"] == "301613"
        assert r["name"] == "新铝时代"
        assert r["notice_date"] == "2024-10-22"
        assert r["report_date"] == "2024-09-30"
        assert r["predict_finance"] == "主营业务收入"
        assert r["predict_lower"] == 1230000000
        assert r["predict_upper"] == 1330000000
        assert r["add_amp_lower"] == 4.73
        assert r["add_amp_upper"] == 13.25
        assert r["predict_type"] == "略增"
        assert r["preyear_same"] is None

    def test_empty_returns_empty_list(self):
        with patch("asgk.earning._datacenter", return_value=[]):
            assert earning_forecast("20240930") == []


class TestEarningExpress:
    def test_filter_uses_iso_date(self):
        with patch("asgk.earning._datacenter", return_value=[]) as m:
            earning_express("20240930")
        assert "(REPORT_DATE='2024-09-30')" in m.call_args.kwargs["filter_str"]

    def test_all_pages_true(self):
        with patch("asgk.earning._datacenter", return_value=[]) as m:
            earning_express("20240930")
        assert m.call_args.kwargs["all_pages"] is True

    def test_report_name(self):
        with patch("asgk.earning._datacenter", return_value=[]) as m:
            earning_express("20240930")
        assert m.call_args.args[0] == "RPT_FCI_PERFORMANCEE"

    def test_field_mapping(self):
        with patch("asgk.earning._datacenter", return_value=[_EXPR_RAW]):
            result = earning_express("20240930")
        assert len(result) == 1
        r = result[0]
        assert r["code"] == "300962"
        assert r["name"] == "中金辐照"
        assert r["notice_date"] == "2024-10-08"
        assert r["eps"] == 0.3632
        assert r["bvps"] == 3.5435
        assert r["operate_income"] == 271524383.77
        assert r["operate_income_sq"] == 258728525.64
        assert r["net_profit"] == 95891712.03
        assert r["net_profit_sq"] == 96507053.17
        assert r["income_yoy"] == 4.945669635131
        assert r["profit_yoy"] == -0.637612609429
        assert r["roe"] == 10.3

    def test_empty_returns_empty_list(self):
        with patch("asgk.earning._datacenter", return_value=[]):
            assert earning_express("20240930") == []
