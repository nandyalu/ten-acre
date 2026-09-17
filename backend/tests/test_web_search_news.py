import pytest

from tradingagents.agents.utils.agent_utils import get_web_search_news
from tradingagents.agents.utils.news_data_tools import get_web_search_news as tool_get_web_search_news


def test_get_web_search_news_tool_signature():
    assert get_web_search_news.name == "get_web_search_news"
    assert tool_get_web_search_news.name == "get_web_search_news"
    arg_names = set(get_web_search_news.args.keys())
    assert "ticker" in arg_names


def test_get_web_search_news_invocation(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.agents.utils.news_data_tools.web_search_financial",
        lambda ticker: f"Search results for {ticker}",
    )
    result = get_web_search_news.invoke({"ticker": "AAPL"})
    assert result == "Search results for AAPL"
