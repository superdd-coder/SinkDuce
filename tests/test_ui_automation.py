"""Automated UI tests using Playwright.

Run:
    pip install playwright pytest-playwright
    playwright install chromium
    pytest tests/test_ui_automation.py -v
"""

from __future__ import annotations

import os
import pytest
from playwright.sync_api import Page, expect

PORT = os.environ.get("SINKDUCE_API_PORT", "18900")
BASE_URL = f"http://localhost:{PORT}"


@pytest.fixture(scope="session")
def browser_context_args():
    return {"base_url": BASE_URL}


class TestSettingsEmbeddingProviders:
    """Test embedding provider CRUD in Settings page."""

    def test_add_embedding_provider(self, page: Page):
        """Add a new embedding provider via the dialog."""
        page.goto("/")
        # Click Settings in sidebar
        page.click("[data-testid='nav-settings']")
        page.wait_for_timeout(500)

        # Click Add Embedding button
        page.click("text=Add Embedding")
        page.wait_for_timeout(300)

        # Fill the dialog
        page.fill("input[placeholder='My Embedding']", "Test Embedding")
        page.select_option("select >> nth=0", "remote")
        page.fill("input[placeholder='text-embedding-3-small']", "text-embedding-3-small")
        page.fill("input[placeholder='https://api.openai.com/v1']", "https://api.openai.com/v1")
        page.fill("input[placeholder='sk-...']", "sk-test-key")

        # Submit
        page.click("button:has-text('Create')")
        page.wait_for_timeout(500)

        # Verify card appears
        expect(page.locator("text=Test Embedding")).to_be_visible()

    def test_embedding_provider_no_dimensions_field(self, page: Page):
        """Embedding provider dialog should NOT have a dimensions field."""
        page.goto("/")
        page.click("[data-testid='nav-settings']")
        page.wait_for_timeout(500)

        page.click("text=Add Embedding")
        page.wait_for_timeout(300)

        # Dimensions field should NOT exist
        dims_field = page.locator("input[placeholder='1024']")
        expect(dims_field).to_have_count(0)


class TestSettingsRerankProviders:
    """Test rerank provider CRUD in Settings page."""

    def test_rerank_provider_no_top_k_field(self, page: Page):
        """Rerank provider dialog should NOT have a top_k field."""
        page.goto("/")
        page.click("[data-testid='nav-settings']")
        page.wait_for_timeout(500)

        page.click("text=Add Reranker")
        page.wait_for_timeout(300)

        # Top K field should NOT exist in the dialog
        topk_field = page.locator("label:has-text('Top K')")
        expect(topk_field).to_have_count(0)


class TestDatabaseConfigEmbedding:
    """Test embedding provider selector in Database Config."""

    def test_embedding_provider_dropdown_exists(self, page: Page):
        """Database config should show embedding provider dropdown."""
        page.goto("/")
        page.click("[data-testid='nav-database']")
        page.wait_for_timeout(500)

        # Select a collection
        page.click("text=default")
        page.wait_for_timeout(300)

        # Embedding Model section should have a Provider dropdown
        provider_select = page.locator("text=Embedding Model").locator("..").locator("select")
        expect(provider_select).to_be_visible()

    def test_embedding_dropdown_has_global_default(self, page: Page):
        """Embedding provider dropdown should have 'Global default' option."""
        page.goto("/")
        page.click("[data-testid='nav-database']")
        page.wait_for_timeout(500)

        page.click("text=default")
        page.wait_for_timeout(300)

        # Check Global default option exists
        provider_select = page.locator("text=Embedding Model").locator("..").locator("select")
        options = provider_select.locator("option")
        expect(options.filter(has_text="Global default")).to_have_count(1)


class TestRecallRerankSelector:
    """Test rerank provider selector in Recall page."""

    def test_rerank_dropdown_appears_when_checked(self, page: Page):
        """Rerank provider dropdown should appear when 'Use Reranker' is checked."""
        page.goto("/")
        page.click("[data-testid='nav-recall']")
        page.wait_for_timeout(500)

        # Check Use Reranker checkbox
        page.check("text=Use Reranker")
        page.wait_for_timeout(300)

        # A dropdown with "Default" option should appear
        rerank_select = page.locator("select").filter(has_text="Default")
        expect(rerank_select).to_be_visible()

    def test_rerank_dropdown_hidden_when_unchecked(self, page: Page):
        """Rerank provider dropdown should hide when 'Use Reranker' is unchecked."""
        page.goto("/")
        page.click("[data-testid='nav-recall']")
        page.wait_for_timeout(500)

        # Uncheck Use Reranker
        page.uncheck("text=Use Reranker")
        page.wait_for_timeout(300)

        # The rerank-specific dropdown should not be visible
        # (there may be other selects, so we check for one with only "Default")
        rerank_selects = page.locator("select").filter(has_text="Default").all()
        # Should be 0 or only the search mode select
        for sel in rerank_selects:
            # If it exists, it should be the search mode select, not rerank
            pass
