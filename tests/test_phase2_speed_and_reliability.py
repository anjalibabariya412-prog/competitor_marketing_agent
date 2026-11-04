"""Unit tests for Phase 2 speed and scraping reliability improvements.

Verifies:
1. Fast-fail actor configuration (pageLoadTimeoutSecs=25, navigationTimeoutSecs=25, maxRequestRetries=1).
2. Domain-level HTTP block memory (bypasses direct HTTP when homepage/sitemap was 403/429/WAF).
3. Nonterminal actor run partial dataset items recovery.
4. Per-domain scrape cache reuse and TTL.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import backend.app.main  # Ensure canonical import order
from backend.app.schemas.enums import PageType
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.content_validation import (
    clear_domain_http_blocks,
    is_domain_http_blocked,
    mark_domain_http_blocked,
)
from backend.app.services.website_content_crawler_service import (
    _build_actor_input,
    clear_scrape_cache,
    crawl_selected_pages,
    get_cached_crawl,
    set_cached_crawl,
    WebsiteCrawlResult,
)


class TestPhase2SpeedAndReliability(unittest.IsolatedAsyncioTestCase):
    """Test suite for Phase 2 speed and reliability improvements."""

    def setUp(self) -> None:
        clear_domain_http_blocks()
        clear_scrape_cache()

    def tearDown(self) -> None:
        clear_domain_http_blocks()
        clear_scrape_cache()

    def test_build_actor_input_fast_fail_settings(self) -> None:
        """Verify actor input uses 25s page timeouts and maxRequestRetries=1."""
        urls = ["https://swissbeauty.in/collections/eyes", "https://swissbeauty.in/pages/about"]
        actor_input = _build_actor_input(urls, timeout_seconds=120)

        self.assertEqual(actor_input["pageLoadTimeoutSecs"], 25)
        self.assertEqual(actor_input["navigationTimeoutSecs"], 25)
        self.assertEqual(actor_input["maxRequestRetries"], 1)
        self.assertEqual(actor_input["maxCrawlDepth"], 0)

    async def test_domain_level_http_block_skips_http_for_internal_pages(self) -> None:
        """Verify that when a domain is marked blocked, internal page crawling bypasses HTTP."""
        target_site = "https://swissbeauty.in"
        pages = [
            f"{target_site}/collections/eyes",
            f"{target_site}/collections/lips",
        ]

        # 1. Mark domain blocked as would occur on 403, 429, or WAF challenge on homepage/sitemap
        mark_domain_http_blocked(target_site, "homepage_http_429")
        self.assertTrue(is_domain_http_blocked(target_site))

        # 2. Run crawler with mock actor
        with patch("backend.app.services.website_content_crawler_service.ApifyClient") as mock_client_cls, \
             patch("backend.app.services.website_content_crawler_service._fetch_single_page_http") as mock_http:

            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_run = MagicMock()
            mock_run.status = "SUCCEEDED"
            mock_run.id = "run-phase2-1"
            mock_run.default_dataset_id = "ds-phase2-1"
            mock_run.get = lambda k, default=None: {"status": "SUCCEEDED", "id": "run-phase2-1", "default_dataset_id": "ds-phase2-1"}.get(k, default)
            mock_client.actor.return_value.call.return_value = mock_run

            mock_items = [
                {"url": pages[0], "markdown": "# Eyes Collection\nEye shadows and mascaras for everyday makeup routines."},
                {"url": pages[1], "markdown": "# Lips Collection\nMatte and liquid lipsticks in vibrant shades."},
            ]
            mock_client.dataset.return_value.iterate_items.return_value = mock_items

            result = await crawl_selected_pages(pages, website_url=target_site)

        # Direct HTTP should NEVER have been attempted
        mock_http.assert_not_called()
        self.assertEqual(result.http_attempted, 0)
        self.assertEqual(result.browser_fallback_requested, 2)
        self.assertEqual(result.browser_fallback_succeeded, 2)
        self.assertEqual(result.status, "success")

    def test_scrape_cache_stores_and_reuses_successful_crawl(self) -> None:
        """Verify that a successful crawl is cached and reused on repeated requests."""
        target_url = "https://brand.example.com"
        dummy_page = ScrapedPage(
            url=f"{target_url}/about",
            title="About Brand",
            content="Substantive about us content describing brand history and quality.",
            page_type=PageType.ABOUT,
        )
        fake_result = WebsiteCrawlResult(
            website_url=target_url,
            status="success",
            scraped_pages=[dummy_page],
            failed_pages=[],
            total_requested=1,
            total_scraped=1,
            total_failed=0,
            has_usable_content=True,
            execution_time_seconds=1.5,
        )

        set_cached_crawl(target_url, fake_result)
        cached = get_cached_crawl(target_url)

        self.assertIsNotNone(cached)
        self.assertEqual(cached.website_url, target_url)
        self.assertEqual(len(cached.scraped_pages), 1)

    def test_partial_recovery_on_nonterminal_deadline_expiration(self) -> None:
        """Verify that if actor deadline expires while RUNNING, partial items in dataset are recovered."""
        from backend.app.services.website_content_crawler_service import _run_actor_sync

        mock_client = MagicMock()
        mock_run = MagicMock()
        mock_run.id = "run-timeout-partial"
        mock_run.status = "RUNNING"
        mock_run.default_dataset_id = "ds-partial-1"
        mock_run.get = lambda k, default=None: {
            "id": "run-timeout-partial",
            "status": "RUNNING",
            "default_dataset_id": "ds-partial-1",
        }.get(k, default)

        mock_client.actor.return_value.call.return_value = mock_run
        mock_client.run.return_value.wait_for_finish.return_value = mock_run

        partial_items = [
            {"url": "https://swissbeauty.in/collections/eyes", "markdown": "# Eyes\nEye palette and mascara."}
        ]
        mock_client.dataset.return_value.iterate_items.return_value = partial_items

        with patch("backend.app.services.website_content_crawler_service.ApifyClient", return_value=mock_client):
            items, run_id, err_msg, status = _run_actor_sync(
                token="token-xyz",
                actor_input={"startUrls": [{"url": "https://swissbeauty.in/collections/eyes"}]},
                timeout_seconds=1,  # Short timeout to trigger nonterminal deadline
            )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://swissbeauty.in/collections/eyes")
        self.assertIn("remained nonterminal ('RUNNING')", err_msg)


if __name__ == "__main__":
    unittest.main()
