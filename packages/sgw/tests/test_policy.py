"""端点策略、凭据保护、并发合并与熔断测试（全程 mock 上游）。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sgw.proxy import EndpointPolicy, Gateway, load_config


CONFIG = Path(__file__).resolve().parent.parent / "sgw" / "config.toml"
PUBLIC_URL = "https://reportapi.eastmoney.com/report/list"


def _config(*, persist: bool = False, cooldown: float = 300) -> dict:
    cfg = load_config(CONFIG)
    cfg["cache"]["persist"] = {"enabled": persist, "tiers": ["P", "L"]}
    cfg["fingerprint"]["enabled"] = False
    cfg["circuit"]["cooldown_seconds"] = cooldown
    # 并发测试不等待真实限流间隔。
    for group in cfg["group"]:
        group["rps"] = 100000
        group["jitter"] = [0, 0]
    return cfg


def _response(status: int = 200, body: bytes = b'{"ok":true}') -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.content = body
    response.headers = {"Content-Type": "application/json"}
    return response


class TestEndpointPolicy:
    def test_unknown_path_fails_closed_without_egress(self):
        gateway = Gateway(_config())
        with patch("sgw.proxy.requests.get") as upstream:
            status, _, _ = gateway.handle(
                "https://reportapi.eastmoney.com/not-reviewed", {}, "P"
            )
        assert status == 403
        upstream.assert_not_called()

    def test_known_host_with_custom_port_is_rejected(self):
        gateway = Gateway(_config())
        with patch("sgw.proxy.requests.get") as upstream:
            status, _, _ = gateway.handle(
                "https://reportapi.eastmoney.com:8443/report/list", {}, "P"
            )
        assert status == 400
        upstream.assert_not_called()

    def test_known_endpoint_exposes_three_axes(self):
        gateway = Gateway(_config())
        policy = gateway.policy_for("reportapi.eastmoney.com", "/report/list")
        assert policy is not None
        assert (policy.review_status, policy.ip_risk, policy.response_scope) == (
            "approved", "controlled", "public"
        )

    def test_invalid_cross_dimension_combination_rejected(self):
        with pytest.raises(ValueError, match="public response must use shared cache"):
            EndpointPolicy.from_config({
                "name": "bad", "host": "example.com", "path": "/x",
                "review_status": "approved", "ip_risk": "controlled",
                "response_scope": "public", "credential_mode": "none",
                "cache_mode": "disabled",
            })


class TestCredentialSafety:
    def test_different_credentials_share_public_cache_and_do_not_persist(self, tmp_path):
        cfg = _config(persist=True)
        cfg["cache"]["persist"]["dir"] = str(tmp_path)
        gateway = Gateway(cfg)
        first_secret = "cookie-first-plaintext"
        second_secret = "csrf-second-plaintext"
        with patch("sgw.proxy.requests.get", return_value=_response()) as upstream:
            a = gateway.handle(PUBLIC_URL, {"page": 1}, "P", {
                "Cookie": first_secret, "X-CSRF-Token": "csrf-a",
            })
            b = gateway.handle(PUBLIC_URL, {"page": 1}, "P", {
                "Cookie": "cookie-b", "X-CSRF-Token": second_secret,
            })
        assert upstream.call_count == 1
        assert a[2]["X-Cache"] == "MISS"
        assert b[2]["X-Cache"] == "HIT-MEM"

        keys = list(gateway.cache._store)
        assert len(keys) == 1
        assert first_secret not in keys[0]
        assert second_secret not in keys[0]
        row = sqlite3.connect(tmp_path / "sgw_cache.db").execute(
            "SELECT key FROM cache"
        ).fetchone()
        assert row is not None
        assert first_secret not in row[0]
        assert second_secret not in row[0]
        gateway.disk_cache.close()

    def test_credential_query_param_excluded_from_public_identity(self):
        gateway = Gateway(_config())
        url = "https://push2ex.eastmoney.com/getTopicZTPool"
        with patch("sgw.proxy.requests.get", return_value=_response()) as upstream:
            gateway.handle(url, {"ut": "secret-a", "date": "20260731"}, "P")
            gateway.handle(url, {"ut": "secret-b", "date": "20260731"}, "P")
        assert upstream.call_count == 1
        assert all("secret-" not in key for key in gateway.cache._store)

    def test_fingerprint_log_contains_no_header_or_query_credentials(self, tmp_path):
        cfg = _config()
        cfg["fingerprint"]["enabled"] = True
        gateway = Gateway(cfg, fp_dir_override=str(tmp_path))
        url = "https://push2ex.eastmoney.com/getTopicZTPool"
        secrets = ("cookie-plaintext", "csrf-plaintext", "query-plaintext")
        with patch("sgw.proxy.requests.get", return_value=_response()):
            gateway.handle(url, {"ut": secrets[2], "date": "20260731"}, "P", {
                "Cookie": secrets[0], "X-CSRF-Token": secrets[1],
            })
        content = next(tmp_path.glob("sgw_fp_*.jsonl")).read_text()
        assert all(secret not in content for secret in secrets)
        record = json.loads(content)
        assert record["key"].startswith("P|https://push2ex.eastmoney.com/getTopicZTPool")


class TestConcurrencyProtection:
    def test_1000_identical_cold_misses_make_one_upstream_request(self):
        gateway = Gateway(_config())
        entered = threading.Event()
        release = threading.Event()

        def slow_response(*_args, **_kwargs):
            entered.set()
            assert release.wait(timeout=5)
            return _response()

        def call():
            return gateway.handle(PUBLIC_URL, {"code": "600519"}, "P")

        with patch("sgw.proxy.requests.get", side_effect=slow_response) as upstream:
            with ThreadPoolExecutor(max_workers=1000) as pool:
                futures = [pool.submit(call) for _ in range(1000)]
                assert entered.wait(timeout=5)
                time.sleep(0.05)
                release.set()
                results = [future.result(timeout=10) for future in futures]
        assert upstream.call_count == 1
        assert all(status == 200 for status, _, _ in results)

    def test_first_429_opens_group_circuit(self):
        gateway = Gateway(_config())
        with patch("sgw.proxy.requests.get", return_value=_response(429)) as upstream:
            first = gateway.handle(PUBLIC_URL, {"page": 1}, "R")
            second = gateway.handle(PUBLIC_URL, {"page": 2}, "R")
        assert first[0] == 429
        assert second[0] == 503
        assert upstream.call_count == 1
        assert gateway.stats()["circuits"]["eastmoney"]["open"] is True

    def test_cooldown_allows_only_one_canary(self):
        gateway = Gateway(_config(cooldown=0.02))
        with patch("sgw.proxy.requests.get", side_effect=[_response(403), _response(200)]) as upstream:
            assert gateway.handle(PUBLIC_URL, {"page": 1}, "R")[0] == 403
            time.sleep(0.03)
            assert gateway.handle(PUBLIC_URL, {"page": 2}, "R")[0] == 200
        assert upstream.call_count == 2
