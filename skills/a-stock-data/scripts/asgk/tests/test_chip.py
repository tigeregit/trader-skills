"""asgk.chip 单元测试。

验证筹码分布的 K 线解析、CYQ 计算、字段映射、adjust 校验。
mock em_get，CYQ 引擎用真实 vendor JS（纯数学，验证算法正确性）。
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from asgk.chip import chip_distribution, _cyq_engine


def _kline_resp(klines: list[str]) -> MagicMock:
    """构造 push2his K 线响应。"""
    r = MagicMock()
    r.json.return_value = {"data": {"klines": klines}}
    return r


# 构造 10 根合成 K 线（真实格式：date,open,close,high,low,vol,amt,zf,zdf,zde,hsl）
def _make_klines(n: int = 10) -> list[str]:
    lines = []
    for i in range(n):
        # 价格在 10-11 之间波动，确保 CYQ 有合理输入
        close = 10.0 + (i % 5) * 0.2
        lines.append(f"2024-01-{i+1:02d},{close-0.1:.2f},{close:.2f},{close+0.1:.2f},{close-0.2:.2f},"
                     f"1000000,10500000,2.0,1.5,0.15,5.0")
    return lines


class TestChipDistribution:
    def test_returns_recent_90(self):
        """返回最近 90 日（即使 K 线更多也截断）。"""
        klines = _make_klines(100)
        with patch("asgk.chip.em_get", return_value=_kline_resp(klines)):
            result = chip_distribution("000001")
        assert len(result) == 90

    def test_field_mapping(self):
        """字段映射完整（benefit_part/avg_cost/pct90/pct70）。"""
        with patch("asgk.chip.em_get", return_value=_kline_resp(_make_klines(5))):
            result = chip_distribution("000001")
        r = result[-1]
        assert "date" in r
        assert "benefit_part" in r
        assert "avg_cost" in r
        assert "pct90_low" in r and "pct90_high" in r and "pct90_concentration" in r
        assert "pct70_low" in r and "pct70_high" in r and "pct70_concentration" in r
        # CYQ 应产生合理数值（非 None/NaN）
        assert isinstance(r["benefit_part"], (int, float))
        assert isinstance(r["avg_cost"], (int, float))

    def test_symbol_pure_number_no_prefix(self):
        """symbol 是纯数字，内部拼 secid（6开头=沪 secid=1.x，否则=深 secid=0.x）。"""
        with patch("asgk.chip.em_get", return_value=_kline_resp(_make_klines(3))) as m:
            chip_distribution("000001")  # 深市
        assert m.call_args.kwargs["params"]["secid"] == "0.000001"

        with patch("asgk.chip.em_get", return_value=_kline_resp(_make_klines(3))) as m:
            chip_distribution("600519")  # 沪市
        assert m.call_args.kwargs["params"]["secid"] == "1.600519"

    def test_adjust_mapping(self):
        """adjust 映射：""→0, qfq→1, hfq→2。"""
        for adj, expected in [("", "0"), ("qfq", "1"), ("hfq", "2")]:
            with patch("asgk.chip.em_get", return_value=_kline_resp(_make_klines(3))) as m:
                chip_distribution("000001", adjust=adj)
            assert m.call_args.kwargs["params"]["fqt"] == expected

    def test_invalid_adjust_raises(self):
        with patch("asgk.chip.em_get", return_value=_kline_resp([])):
            try:
                chip_distribution("000001", adjust="invalid")
                assert False, "应抛 ValueError"
            except ValueError:
                pass

    def test_empty_klines_returns_empty(self):
        with patch("asgk.chip.em_get", return_value=_kline_resp([])):
            assert chip_distribution("000001") == []

    def test_lmt_210(self):
        """拉取近 210 根 K 线。"""
        with patch("asgk.chip.em_get", return_value=_kline_resp(_make_klines(3))) as m:
            chip_distribution("000001")
        assert m.call_args.kwargs["params"]["lmt"] == "210"


class TestCyqEngine:
    def test_engine_cached(self):
        """_cyq_engine 用 lru_cache，多次调用返回同一实例。"""
        a = _cyq_engine()
        b = _cyq_engine()
        assert a is b

    def test_cyq_computes_valid_output(self):
        """CYQ 对合成 K 线应产生有效输出（benefitPart 0-1, avgCost 正数）。"""
        js = _cyq_engine()
        records = []
        for i, line in enumerate(_make_klines(5)):
            p = line.split(",")
            records.append({"index": i, "date": p[0], "open": float(p[1]), "close": float(p[2]),
                            "high": float(p[3]), "low": float(p[4]), "volume": float(p[5]),
                            "volume_money": float(p[6]), "zf": float(p[7]), "zdf": float(p[8]),
                            "zde": float(p[9]), "hsl": float(p[10])})
        mcode = js.call("CYQCalculator", len(records) - 1, records)
        assert 0 <= mcode["benefitPart"] <= 1
        # CYQ 的 avgCost 返回字符串（如 '10.38'），用 _to_float 转换后应 > 0
        from asgk.chip import _to_float
        assert _to_float(mcode["avgCost"]) > 0
        assert "percentChips" in mcode
        assert "90" in mcode["percentChips"] and "70" in mcode["percentChips"]
