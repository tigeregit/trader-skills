"""风控源请求入口的失败关闭行为。"""
from unittest.mock import patch

import pytest

from asgk import em_proxy


def test_missing_gateway_never_falls_back_to_direct(monkeypatch):
    monkeypatch.setattr(em_proxy, "_GW", None)
    # 旧版本的逃生开关即使残留在部署环境也必须失效。
    monkeypatch.setenv("ASGK_ALLOW_DIRECT", "1")
    with patch("asgk.em_proxy.requests.get") as direct:
        with pytest.raises(RuntimeError, match="禁止直连"):
            em_proxy.em_get("https://push2.eastmoney.com/api/qt/stock/get")
    direct.assert_not_called()
