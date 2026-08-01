"""新闻接口响应兼容测试（离线）。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from asgk.news import eastmoney_stock_news


def _response(text: str) -> MagicMock:
    response = MagicMock()
    response.text = text
    return response


def test_stock_news_parses_jsonp():
    payload = {"result": {"cmsArticleWebOld": [{
        "title": "<em>贵州茅台</em>公告", "content": "内容", "date": "2026-08-01",
        "mediaName": "测试媒体", "url": "https://example.test/news",
    }]}}
    text = "jQuery_news(" + json.dumps(payload, ensure_ascii=False) + ")"
    with patch("asgk.news.em_get", return_value=_response(text)):
        rows = eastmoney_stock_news("600519", 1)
    assert rows[0]["title"] == "贵州茅台公告"


def test_stock_news_parses_jsonp_with_semicolon():
    payload = {"result": {"cmsArticleWebOld": []}}
    text = "jQuery_news(" + json.dumps(payload) + ");"
    with patch("asgk.news.em_get", return_value=_response(text)):
        assert eastmoney_stock_news("600519", 1) == []


def test_stock_news_passport_response_returns_empty():
    text = json.dumps({"result": {"passportWeb": [{"uid": ""}]}})
    with patch("asgk.news.em_get", return_value=_response(text)):
        assert eastmoney_stock_news("600519", 1) == []


def test_stock_news_non_json_response_returns_empty():
    with patch("asgk.news.em_get", return_value=_response("blocked")):
        assert eastmoney_stock_news("600519", 1) == []
