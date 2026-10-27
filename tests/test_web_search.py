"""Tests for Chat web search (Tavily + HITL confirm)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from src.chatbox.web_search import (
    WebSearchConfirmStore,
    format_web_results_for_llm,
    tavily_search,
    web_results_to_sources,
)


class TestTavilySearch:
    def test_empty_query(self):
        out = tavily_search("", api_key="k")
        assert out["error"]
        assert out["results"] == []

    def test_missing_key(self):
        out = tavily_search("hello", api_key="")
        assert "API key" in out["error"]

    def test_success_normalizes_results(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {
                    "title": "Example",
                    "url": "https://example.com",
                    "content": "Hello world",
                    "score": 0.9,
                }
            ]
        }
        with patch("src.chatbox.web_search.httpx.Client") as Client:
            client = MagicMock()
            client.__enter__ = MagicMock(return_value=client)
            client.__exit__ = MagicMock(return_value=False)
            client.post.return_value = mock_resp
            Client.return_value = client
            out = tavily_search("hello", api_key="tvly-test", max_results=3)

        assert out["provider"] == "tavily"
        assert out["source_type"] == "web"
        assert len(out["results"]) == 1
        assert out["results"][0]["url"] == "https://example.com"
        assert out["results"][0]["source_type"] == "web"


class TestFormatAndSources:
    def test_format_contains_banner(self):
        text = format_web_results_for_llm({
            "query": "q",
            "provider": "tavily",
            "results": [{"title": "T", "url": "https://x", "content": "c"}],
        })
        assert "WEB" in text or "INTERNET" in text
        assert "not knowledge base" in text.lower() or "NOT from the private" in text
        assert "https://x" in text

    def test_sources_tagged_web(self):
        sources = web_results_to_sources({
            "results": [
                {"title": "T", "url": "https://x", "content": "body", "score": 0.5}
            ]
        })
        assert sources[0]["metadata"]["source_type"] == "web"
        assert sources[0]["metadata"]["url"] == "https://x"


class TestConfirmStore:
    def test_approve(self):
        store = WebSearchConfirmStore()
        cid = store.create("my query")

        async def _run():
            wait_task = asyncio.create_task(store.wait(cid, timeout=5))
            await asyncio.sleep(0.05)
            assert store.resolve(cid, True)
            return await wait_task

        assert asyncio.run(_run()) is True

    def test_decline(self):
        store = WebSearchConfirmStore()
        cid = store.create("q")

        async def _run():
            wait_task = asyncio.create_task(store.wait(cid, timeout=5))
            await asyncio.sleep(0.05)
            store.resolve(cid, False)
            return await wait_task

        assert asyncio.run(_run()) is False

    def test_unknown_id(self):
        store = WebSearchConfirmStore()
        assert store.resolve("missing", True) is False


class TestWebToolAllowlist:
    def test_tool_marked_disabled_when_chat_toggle_off(self):
        """Tool stays listed so the agent sees web_toggle=disabled (not missing)."""
        from src.chatbox.query_tools import tools_for_mode, WEB_SEARCH_TOOL_NAME

        with patch("src.chatbox.web_search.has_web_search_api_key", return_value=True):
            tools = tools_for_mode("agentic", web_search_enabled=False)
            by_name = {t["function"]["name"]: t for t in tools}
            assert WEB_SEARCH_TOOL_NAME in by_name
            desc = by_name[WEB_SEARCH_TOOL_NAME]["function"]["description"]
            assert "web_toggle=disabled" in desc or "status=disabled" in desc

    def test_tool_marked_disabled_without_api_key(self):
        from src.chatbox.query_tools import tools_for_mode, WEB_SEARCH_TOOL_NAME

        with patch("src.chatbox.web_search.has_web_search_api_key", return_value=False):
            tools = tools_for_mode("agentic", web_search_enabled=True)
            by_name = {t["function"]["name"]: t for t in tools}
            assert WEB_SEARCH_TOOL_NAME in by_name
            desc = by_name[WEB_SEARCH_TOOL_NAME]["function"]["description"]
            assert "disabled" in desc.lower()

    def test_tool_marked_enabled_when_toggle_on_and_key(self):
        from src.chatbox.query_tools import tools_for_mode, WEB_SEARCH_TOOL_NAME

        with patch("src.chatbox.web_search.has_web_search_api_key", return_value=True):
            tools = tools_for_mode("agentic", web_search_enabled=True)
            by_name = {t["function"]["name"]: t for t in tools}
            assert WEB_SEARCH_TOOL_NAME in by_name
            desc = by_name[WEB_SEARCH_TOOL_NAME]["function"]["description"]
            assert "web_toggle=enabled" in desc or "status=enabled" in desc
            qnames = {
                t["function"]["name"]
                for t in tools_for_mode("direct", web_search_enabled=True)
            }
            assert WEB_SEARCH_TOOL_NAME in qnames
            mnames = {
                t["function"]["name"]
                for t in tools_for_mode(
                    "direct", is_meeting=True, web_search_enabled=True
                )
            }
            assert WEB_SEARCH_TOOL_NAME not in mnames

    def test_format_web_tool_result_distinguishes_decline_vs_disabled(self):
        from src.chatbox.web_search import format_web_tool_result

        declined = format_web_tool_result(
            status="user_declined",
            web_toggle="enabled",
            query="bioenergy trends",
            message="User clicked Decline",
        )
        assert "status: user_declined" in declined
        assert "web_toggle: enabled" in declined
        disabled = format_web_tool_result(
            status="disabled",
            web_toggle="disabled",
            message="Web toggle is OFF",
        )
        assert "status: disabled" in disabled
        assert "web_toggle: disabled" in disabled


class TestWebSearchConfigModel:
    def test_default_off(self):
        from src.config import AppConfig

        cfg = AppConfig()
        assert cfg.web_search.enabled is False
        assert cfg.web_search.api_key == ""
        assert cfg.web_search.provider == "tavily"
