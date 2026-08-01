"""asgk.board 单元测试。

验证板块成份股的名称解析、分页、字段映射。mock em_get，不打外网。
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from asgk.board import board_constituents, _resolve_board_code
import pytest


def _push2_resp(diff: list, total: int = 0) -> MagicMock:
    r = MagicMock()
    r.json.return_value = {"data": {"diff": diff, "total": total}}
    return r


# 名称辅助响应（融资融券 → BK0655）
_NAME_DIFF = [{"f12": "BK0654", "f14": "融资融券概念"}, {"f12": "BK0655", "f14": "融资融券"}]
# 成份股响应（融资融券板块的几只）
_CONS_DIFF = [
    {"f12": "920924", "f14": "广脉科技", "f2": 13.28, "f3": -0.45, "f5": 10000,
     "f6": 132800, "f7": 2.1, "f8": 1.5, "f15": 13.5, "f16": 13.0, "f17": 13.2},
    {"f12": "688651", "f14": "盛邦安全", "f2": 20.33, "f3": -1.45, "f5": 8000,
     "f6": 162640, "f7": 3.2, "f8": 2.1, "f15": 20.8, "f16": 20.0, "f17": 20.5},
]


class TestResolveBoardCode:
    def test_bk_code_passes_through(self):
        """BK 开头的代码直接返回，不查辅助表。"""
        assert _resolve_board_code("BK0655", "concept") == "BK0655"

    def test_name_resolved_from_helper(self):
        with patch("asgk.board.em_get", return_value=_push2_resp(_NAME_DIFF, total=2)):
            code = _resolve_board_code("融资融券", "concept")
        assert code == "BK0655"

    def test_name_resolved_with_display_suffix(self):
        diff = [{"f12": "BK0655", "f14": "融资融券概念"}]
        with patch("asgk.board.em_get", return_value=_push2_resp(diff, total=1)):
            assert _resolve_board_code("融资融券", "concept") == "BK0655"

    def test_concept_vs_industry_fs(self):
        """概念用 t:3，行业用 t:2。"""
        # 概念样本
        with patch("asgk.board.em_get", return_value=_push2_resp(_NAME_DIFF, total=2)) as m:
            _resolve_board_code("融资融券", "concept")
        assert "t:3" in m.call_args.kwargs["params"]["fs"]

        # 行业样本（用行业名称）
        ind_diff = [{"f12": "BK1027", "f14": "小金属"}]
        with patch("asgk.board.em_get", return_value=_push2_resp(ind_diff, total=1)) as m:
            _resolve_board_code("小金属", "industry")
        assert "t:2" in m.call_args.kwargs["params"]["fs"]

    def test_name_not_found_raises(self):
        with patch("asgk.board.em_get", return_value=_push2_resp(_NAME_DIFF, total=2)):
            try:
                _resolve_board_code("不存在的板块", "concept")
                assert False, "应抛 ValueError"
            except ValueError:
                pass


class TestBoardConstituents:
    def test_field_mapping(self):
        """成份股字段映射（f12→code 等）。"""
        with patch("asgk.board._resolve_board_code", return_value="BK0655"), \
             patch("asgk.board.em_get", return_value=_push2_resp(_CONS_DIFF, total=2)):
            result = board_constituents("融资融券", "concept")
        assert len(result) == 2
        r = result[0]
        assert r["code"] == "920924"
        assert r["name"] == "广脉科技"
        assert r["price"] == 13.28
        assert r["pct"] == -0.45
        assert r["vol"] == 10000
        assert r["amount"] == 132800
        assert r["amplitude"] == 2.1
        assert r["turnover"] == 1.5
        assert r["high"] == 13.5
        assert r["low"] == 13.0
        assert r["open"] == 13.2

    def test_pagination_multi_page(self):
        """多页：第一页 pz=100 total=130，第二页取剩余 30。"""
        page2_diff = [{"f12": "688620", "f14": "罗普特", "f2": 11.59, "f3": -3.98}]
        responses = [
            _push2_resp(_CONS_DIFF, total=130),  # page1, 2 条（测试用），total=130
            _push2_resp(page2_diff, total=130),  # page2
        ]
        with patch("asgk.board._resolve_board_code", return_value="BK0655"), \
             patch("asgk.board.em_get", side_effect=responses) as m:
            result = board_constituents("融资融券", "concept")
        # 应翻 2 页（page1 100<130 继续，page2 200>=130 停）
        assert m.call_count == 2
        assert len(result) == 3  # 2 + 1

    def test_uses_unnumbered_push2(self):
        """主用无编号 push2.eastmoney.com（[§7 决策7]）。"""
        with patch("asgk.board._resolve_board_code", return_value="BK0655"), \
             patch("asgk.board.em_get", return_value=_push2_resp(_CONS_DIFF, total=2)) as m:
            board_constituents("融资融券", "concept")
        assert "push2.eastmoney.com" in m.call_args.args[0]
        assert "29.push2" not in m.call_args.args[0]

    def test_fs_uses_board_code(self):
        """成份股 fs 含 b:{板块代码}。"""
        with patch("asgk.board._resolve_board_code", return_value="BK0655"), \
             patch("asgk.board.em_get", return_value=_push2_resp(_CONS_DIFF, total=2)) as m:
            board_constituents("融资融券", "concept")
        assert "b:BK0655" in m.call_args.kwargs["params"]["fs"]

    def test_empty_returns_empty_list(self):
        with patch("asgk.board._resolve_board_code", return_value="BK0655"), \
             patch("asgk.board.em_get", return_value=_push2_resp([], total=0)):
            assert board_constituents("融资融券", "concept") == []

    def test_null_data_is_not_reported_as_empty(self):
        response = MagicMock()
        response.json.return_value = {"rc": 0, "data": None}
        with patch("asgk.board._resolve_board_code", return_value="BK0655"), \
             patch("asgk.board.em_get", return_value=response), \
             pytest.raises(RuntimeError, match="data=null"):
            board_constituents("融资融券", "concept")
