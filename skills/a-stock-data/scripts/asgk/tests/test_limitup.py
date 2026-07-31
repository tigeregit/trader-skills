"""涨停池端点白名单回归测试。"""
from unittest.mock import MagicMock, patch

import pytest

from asgk.limitup import _em_zt_api


def test_known_pool_uses_classified_url():
    response = MagicMock()
    response.json.return_value = {"data": None}
    with patch("asgk.limitup.em_get", return_value=response) as em_get:
        assert _em_zt_api("getTopicZTPool", "fbt:asc", "20260731") == []
    assert em_get.call_args.args == (
        "https://push2ex.eastmoney.com/getTopicZTPool",
    )


def test_unknown_pool_fails_before_gateway_call():
    with patch("asgk.limitup.em_get") as em_get:
        with pytest.raises(ValueError, match="未分类"):
            _em_zt_api("unreviewedPool", "fbt:asc", "20260731")
    em_get.assert_not_called()
