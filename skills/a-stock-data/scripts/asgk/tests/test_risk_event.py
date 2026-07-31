"""asgk.risk_event 单元测试。

验证高管增减持/回购/机构调研的字段映射、filter 转换、all_pages 传入。
mock _datacenter，不打外网。字段名基于真机确认（2026-07-31）。
"""
from __future__ import annotations

from unittest.mock import patch

from asgk.risk_event import mgmt_trade, repurchase, institute_research


# 高管增减持真机样本（RPT_EXECUTIVE_HOLD_DETAILS）
_MGMT_RAW = {
    "SECURITY_CODE": "688091", "SECURITY_NAME": "上海谊众",
    "CHANGE_DATE": "2026-07-30 00:00:00", "PERSON_NAME": "周劲松",
    "POSITION_NAME": "董事", "CHANGE_SHARES": 3000, "AVERAGE_PRICE": 36.74,
    "CHANGE_AMOUNT": 110220, "CHANGE_REASON": "二级市场买卖",
    "CHANGE_RATIO": 0.0015, "CHANGE_AFTER_HOLDNUM": 40130058,
    "HOLD_TYPE": "A股",
}

# 回购真机样本（RPTA_WEB_GETHGLIST_NEW）
_REPUR_RAW = {
    "DIM_SCODE": "688018", "SECURITYSHORTNAME": "乐鑫科技",
    "UPDATEDATE": "2026-07-31 00:00:00", "REPURSTARTDATE": "2026-07-30 00:00:00",
    "REPURENDDATE": "2026-10-30 00:00:00", "REPURPROGRESS": "001",
    "REPURAMOUNTLOWER": 50000000, "REPURAMOUNTLIMIT": 100000000,
    "REPURNUMLOWER": 412300, "REPURNUMCAP": 824500,
    "REPURPRICECAP": 121.28, "REPURAMOUNT": None, "REPURNUM": None,
}

# 机构调研究机样本（RPT_ORG_SURVEY, columns=ALL）
_JGDY_RAW = {
    "SECURITY_CODE": "603517", "SECURITY_NAME_ABBR": "ST绝味",
    "NOTICE_DATE": "2026-07-31 00:00:00", "RECEIVE_START_DATE": "2026-07-29 00:00:00",
    "RECEIVE_OBJECT": "湘财证券", "RECEIVE_PLACE": "公司29楼会议室",
    "RECEIVE_WAY_EXPLAIN": "特定对象调研", "INVESTIGATORS": None,
    "RECEPTIONIST": "副总裁、董事会秘书 廖凯", "ORG_TYPE": "证券公司",
}


class TestMgmtTrade:
    def test_all_pages_true(self):
        with patch("asgk.risk_event._datacenter", return_value=[]) as m:
            mgmt_trade()
        assert m.call_args.kwargs["all_pages"] is True

    def test_report_name(self):
        with patch("asgk.risk_event._datacenter", return_value=[]) as m:
            mgmt_trade()
        assert m.call_args.args[0] == "RPT_EXECUTIVE_HOLD_DETAILS"

    def test_field_mapping(self):
        with patch("asgk.risk_event._datacenter", return_value=[_MGMT_RAW]):
            result = mgmt_trade()
        r = result[0]
        assert r["code"] == "688091"
        assert r["name"] == "上海谊众"
        assert r["change_date"] == "2026-07-30"
        assert r["person"] == "周劲松"
        assert r["position"] == "董事"
        assert r["change_shares"] == 3000
        assert r["avg_price"] == 36.74
        assert r["change_amount"] == 110220
        assert r["change_reason"] == "二级市场买卖"
        assert r["change_ratio"] == 0.0015
        assert r["hold_after"] == 40130058
        assert r["hold_type"] == "A股"

    def test_empty_returns_empty_list(self):
        with patch("asgk.risk_event._datacenter", return_value=[]):
            assert mgmt_trade() == []


class TestRepurchase:
    def test_all_pages_true(self):
        with patch("asgk.risk_event._datacenter", return_value=[]) as m:
            repurchase()
        assert m.call_args.kwargs["all_pages"] is True

    def test_report_name(self):
        with patch("asgk.risk_event._datacenter", return_value=[]) as m:
            repurchase()
        assert m.call_args.args[0] == "RPTA_WEB_GETHGLIST_NEW"

    def test_field_mapping(self):
        with patch("asgk.risk_event._datacenter", return_value=[_REPUR_RAW]):
            result = repurchase()
        r = result[0]
        assert r["code"] == "688018"
        assert r["name"] == "乐鑫科技"
        assert r["notice_date"] == "2026-07-31"
        assert r["start_date"] == "2026-07-30"
        assert r["end_date"] == "2026-10-30"
        assert r["progress"] == "董事会预案"  # 001 映射（akshare 真实代码表）
        assert r["plan_amt_lower"] == 50000000
        assert r["plan_amt_upper"] == 100000000
        assert r["plan_num_lower"] == 412300
        assert r["plan_num_upper"] == 824500
        assert r["price_cap"] == 121.28
        assert r["done_amt"] is None

    def test_progress_all_codes(self):
        """全部 6 个真实进度代码正确映射（akshare stock_repurchase_em.py:94-101）。"""
        codes = {"001": "董事会预案", "002": "股东大会通过", "003": "股东大会否决",
                 "004": "实施中", "005": "停止实施", "006": "完成实施"}
        for code, expected in codes.items():
            raw = {**_REPUR_RAW, "REPURPROGRESS": code}
            with patch("asgk.risk_event._datacenter", return_value=[raw]):
                assert repurchase()[0]["progress"] == expected

    def test_unknown_progress_keeps_raw(self):
        """未知进度代码保留原始值。"""
        raw = {**_REPUR_RAW, "REPURPROGRESS": "999"}
        with patch("asgk.risk_event._datacenter", return_value=[raw]):
            result = repurchase()
        assert result[0]["progress"] == "999"

    def test_empty_returns_empty_list(self):
        with patch("asgk.risk_event._datacenter", return_value=[]):
            assert repurchase() == []


class TestInstituteResearch:
    def test_filter_uses_iso_date(self):
        """filter 含 IS_SOURCE 和 RECEIVE_START_DATE 大于比较。"""
        with patch("asgk.risk_event._datacenter", return_value=[]) as m:
            institute_research("20241201")
        f = m.call_args.kwargs["filter_str"]
        assert "(IS_SOURCE=\"1\")" in f
        assert "(RECEIVE_START_DATE>'2024-12-01')" in f

    def test_all_pages_true(self):
        with patch("asgk.risk_event._datacenter", return_value=[]) as m:
            institute_research("20241201")
        assert m.call_args.kwargs["all_pages"] is True

    def test_report_name(self):
        with patch("asgk.risk_event._datacenter", return_value=[]) as m:
            institute_research("20241201")
        assert m.call_args.args[0] == "RPT_ORG_SURVEY"

    def test_field_mapping(self):
        with patch("asgk.risk_event._datacenter", return_value=[_JGDY_RAW]):
            result = institute_research("20241201")
        r = result[0]
        assert r["code"] == "603517"
        assert r["name"] == "ST绝味"
        assert r["notice_date"] == "2026-07-31"
        assert r["receive_date"] == "2026-07-29"
        assert r["receive_object"] == "湘财证券"
        assert r["receive_place"] == "公司29楼会议室"
        assert r["receive_way"] == "特定对象调研"
        assert r["investigators"] == ""
        assert r["receptionist"] == "副总裁、董事会秘书 廖凯"
        assert r["org_type"] == "证券公司"

    def test_empty_returns_empty_list(self):
        with patch("asgk.risk_event._datacenter", return_value=[]):
            assert institute_research("20241201") == []
