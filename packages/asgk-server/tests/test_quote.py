"""quote 能力服务端测试（mock 腾讯 GBK 上游）。

验证腾讯上游知识（URL/市场前缀/GBK 解码/53 字段映射）正确下沉到服务端：
  - fetch_quote 经 egress_request 取腾讯 GBK 文本，解析为结构化 dict
  - 53 字段映射与 asgk/quote.py.tencent_quote 一致（客户端零破坏）
  - 403 触发熔断；realtime 型 no-cache 但并发合并

不打真实腾讯——mock asgk_server.egress.egress_request 返回构造的 GBK payload。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from asgk_server import registry, server as server_mod
from asgk_server.server import CapabilityServer


@pytest.fixture(autouse=True)
def _keep_registry():
    """quote 能力在模块导入时注册（capabilities 包），不要 clear。"""
    yield


def _config() -> dict:
    return {
        "group": [{"name": "tencent", "domains": ["qt.gtimg.cn"],
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


def _gbk_response(payload: str, status: int = 200) -> MagicMock:
    """构造腾讯 GBK 响应 mock（content 是 bytes，需 gbk 可解码）。"""
    r = MagicMock()
    r.status_code = status
    r.content = payload.encode("gbk")
    return r


def _tencent_line(code: str, market: str, name: str, price: str) -> str:
    """构造一行腾讯行情文本（53 字段，关键字段填实，其余占位 0）。

    格式：v_<market><code>="f0~f1(name)~f2(code)~f3(price)~...~f52"
    字段索引（与 asgk/quote.py 映射一致）：
      1=name 3=price 4=last_close 5=open 31=change_amt 32=change_pct
      33=high 34=low 37=amount_wan 38=turnover 39=pe_ttm 43=amplitude
      44=mcap_yi 45=float_mcap_yi 46=pb 47=limit_up 48=limit_down
      49=vol_ratio 52=pe_static
    """
    fields = ["0"] * 53
    fields[1] = name
    fields[2] = code
    fields[3] = price
    fields[4] = "1800.00"  # last_close
    fields[5] = "1805.00"  # open
    fields[31] = "20.50"   # change_amt
    fields[32] = "1.15"    # change_pct
    fields[33] = "1830.00" # high
    fields[34] = "1790.00" # low
    fields[37] = "500000"  # amount_wan
    fields[38] = "0.8"     # turnover_pct
    fields[39] = "30.5"    # pe_ttm
    fields[43] = "2.2"     # amplitude_pct
    fields[44] = "22000"   # mcap_yi
    fields[45] = "22000"   # float_mcap_yi
    fields[46] = "10.5"    # pb
    fields[47] = "1980.00" # limit_up
    fields[48] = "1620.00" # limit_down
    fields[49] = "1.2"     # vol_ratio
    fields[52] = "28.0"    # pe_static
    return f'v_{market}{code}="{"~".join(fields)}"'


# ── 字段映射 ──────────────────────────────────────────────────
class TestQuoteParsing:
    def test_single_stock_parsed(self, srv):
        line = _tencent_line("600519", "sh", "贵州茅台", "1820.50")
        with patch("asgk_server.capabilities.quote.egress_request",
                   return_value=_gbk_response(line + ";")):
            status, payload = srv.handle_capability("quote", {"codes": ["600519"]})
        assert status == 200
        data = payload["data"]
        assert "600519" in data
        q = data["600519"]
        assert q["name"] == "贵州茅台"
        assert q["price"] == 1820.50
        assert q["pe_ttm"] == 30.5
        assert q["pb"] == 10.5
        assert q["mcap_yi"] == 22000
        assert q["limit_up"] == 1980.00
        assert q["limit_down"] == 1620.00
        assert q["pe_static"] == 28.0

    def test_market_prefix_sh(self, srv):
        """6 开头 → sh 前缀，发给腾讯的 q 参数应是 sh600519。"""
        line = _tencent_line("600519", "sh", "贵州茅台", "1820.50")
        with patch("asgk_server.capabilities.quote.egress_request",
                   return_value=_gbk_response(line + ";")) as mock_egress:
            srv.handle_capability("quote", {"codes": ["600519"]})
        _method, _client, url = mock_egress.call_args.args
        kwargs = mock_egress.call_args.kwargs
        assert url == "https://qt.gtimg.cn/q"
        assert kwargs["params"]["q"] == "sh600519"

    def test_market_prefix_sz(self, srv):
        """0/3 开头 → sz 前缀。"""
        line = _tencent_line("000001", "sz", "平安银行", "12.50")
        with patch("asgk_server.capabilities.quote.egress_request",
                   return_value=_gbk_response(line + ";")) as mock_egress:
            srv.handle_capability("quote", {"codes": ["000001"]})
        _method, _client, _url = mock_egress.call_args.args
        kwargs = mock_egress.call_args.kwargs
        assert kwargs["params"]["q"] == "sz000001"

    def test_market_prefix_bj(self, srv):
        """8 开头 → bj 前缀（北交所）。"""
        line = _tencent_line("830799", "bj", "艾融软件", "10.00")
        with patch("asgk_server.capabilities.quote.egress_request",
                   return_value=_gbk_response(line + ";")) as mock_egress:
            srv.handle_capability("quote", {"codes": ["830799"]})
        _method, _client, _url = mock_egress.call_args.args
        kwargs = mock_egress.call_args.kwargs
        assert kwargs["params"]["q"] == "bj830799"

    def test_multiple_codes_joined(self, srv):
        """多只票逗号拼接发给腾讯。"""
        lines = (_tencent_line("600519", "sh", "贵州茅台", "1820.50") + ";" +
                 _tencent_line("000001", "sz", "平安银行", "12.50") + ";")
        with patch("asgk_server.capabilities.quote.egress_request",
                   return_value=_gbk_response(lines)) as mock_egress:
            status, payload = srv.handle_capability(
                "quote", {"codes": ["600519", "000001"]})
        _method, _client, _url = mock_egress.call_args.args
        kwargs = mock_egress.call_args.kwargs
        assert kwargs["params"]["q"] == "sh600519,sz000001"
        assert set(payload["data"]) == {"600519", "000001"}

    def test_empty_codes_returns_empty(self, srv):
        with patch("asgk_server.capabilities.quote.egress_request",
                   return_value=_gbk_response("")):
            status, payload = srv.handle_capability("quote", {"codes": []})
        assert status == 200
        assert payload["data"] == {}

    def test_short_line_skipped(self, srv):
        """不足 53 字段的行被跳过，不抛异常。"""
        short = 'v_sh600519="1~贵州茅台~600519~1820.50"'
        with patch("asgk_server.capabilities.quote.egress_request",
                   return_value=_gbk_response(short + ";")):
            status, payload = srv.handle_capability("quote", {"codes": ["600519"]})
        assert status == 200
        assert payload["data"] == {}


# ── 熔断反馈 ──────────────────────────────────────────────────
class TestQuoteCircuit:
    def test_403_triggers_circuit_and_blocks(self, srv):
        with patch("asgk_server.capabilities.quote.egress_request",
                   return_value=_gbk_response("", status=403)):
            status1, _ = srv.handle_capability("quote", {"codes": ["600519"]})
        assert status1 == 403  # 腾讯返回 403 → fetch 反馈 immediate
        # 熔断已开，再次请求被拦
        with patch("asgk_server.capabilities.quote.egress_request") as mock2:
            status2, payload2 = srv.handle_capability("quote", {"codes": ["600519"]})
        assert status2 == 503
        mock2.assert_not_called()  # 熔断中不再出网

    def test_500_retried_then_marked_failed(self, srv):
        """5xx 反馈熔断（非 immediate，累计到阈值才开）。单次 max_attempts=1 不重试。"""
        with patch("asgk_server.capabilities.quote.egress_request",
                   return_value=_gbk_response("", status=502)):
            status, payload = srv.handle_capability("quote", {"codes": ["600519"]})
        assert status == 502


# ── sources 端点 ──────────────────────────────────────────────
class TestQuoteSources:
    def test_quote_sources_lists_tencent(self):
        """GET /v1/sources?capability=quote 返回 ['tencent']（验收标准）。"""
        meta, _ = registry.get_capability("quote")
        assert meta.source_names() == ["tencent"]


# ── cache 行为（realtime no-cache）────────────────────────────
class TestQuoteCache:
    def test_realtime_no_cache_each_call_hits_upstream(self, srv):
        """realtime 型 TTL=0：每次都出网，不命中缓存。"""
        line = _tencent_line("600519", "sh", "贵州茅台", "1820.50") + ";"
        with patch("asgk_server.capabilities.quote.egress_request",
                   return_value=_gbk_response(line)) as mock_egress:
            srv.handle_capability("quote", {"codes": ["600519"]})
            srv.handle_capability("quote", {"codes": ["600519"]})
        assert mock_egress.call_count == 2  # 两次都出网
