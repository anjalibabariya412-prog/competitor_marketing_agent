"""Offline regression tests for Apify crawler timeout, nonterminal status, and dataset error handling.

Covers all 12 scenarios required:
1. Actor returns SUCCEEDED and dataset retrieval succeeds.
2. Actor returns SUCCEEDED but dataset retrieval raises an exception.
3. Actor returns TIMED-OUT with recoverable partial items.
4. Actor returns TIMED-OUT without recoverable items.
5. Actor initially returns a nonterminal status and later reaches SUCCEEDED.
6. Actor initially returns a nonterminal status and later reaches TIMED-OUT with partial results.
7. Actor remains nonterminal until the overall deadline expires.
8. The implementation does not start a duplicate actor run while the original run is still active.
9. Missing dataset ID and empty dataset are reported distinctly.
10. Genuine FAILED and ABORTED statuses remain distinguishable from timeouts.
11. Existing retry behavior remains bounded and avoids redundant full crawls after partial recovery.
12. Existing API behavior remains correct for sufficient partial content and insufficient readable content.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

import backend.app.main  # Ensure canonical import order
from backend.app.agent.nodes.business_overview_node import business_overview_node
from backend.app.agent.nodes.website_crawling_node import website_crawling_node
from backend.app.agent.state import AgentState
from backend.app.main import app
from backend.app.schemas.business_form import BusinessForm
from backend.app.schemas.enums import PageType
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.business_overview_service import (
    BusinessOverview,
    BusinessOverviewResult,
    ProductServiceItem,
    TargetAudienceInfo,
)
from backend.app.services.page_selection_service import SelectedPage
from backend.app.services.sitemap_fallback_service import WebsiteURLDiscoveryResult
from backend.app.services.website_content_crawler_service import (
    FailedPageScrape,
    WebsiteCrawlResult,
    _run_actor_sync,
    crawl_selected_pages,
)


def _make_mock_run(
    run_id: str = "run-mock-123",
    status: str = "SUCCEEDED",
    dataset_id: str | None = "ds-mock-456",
    status_message: str = "",
) -> MagicMock:
    """Create a mock Apify Run object supporting both attribute and dict lookups."""
    run = MagicMock()
    run.id = run_id
    run.status = status
    run.default_dataset_id = dataset_id
    run.status_message = status_message
    run.get = lambda k, default=None: {
        "id": run_id,
        "status": status,
        "default_dataset_id": dataset_id,
        "defaultDatasetId": dataset_id,
        "status_message": status_message,
        "statusMessage": status_message,
    }.get(k, default)
    return run


class TestCrawlerTimeoutAndPartialResults(unittest.IsolatedAsyncioTestCase):
    """Test suite validating resilient crawler timeout handling, nonterminal statuses, and dataset errors."""

    def setUp(self) -> None:
        self.api_client = TestClient(app)
        self.sample_form = BusinessForm(
            business_name_and_description="Swiss Beauty cosmetics brand offering affordable makeup.",
            product_or_service="Lip glosses, blush, and foundation",
            target_audience="Women seeking trendy beauty products",
            audience_unknown=False,
            business_website="https://swissbeauty.example.com",
        )
        self.selected_pages = [
            SelectedPage(url="https://swissbeauty.example.com/", page_type=PageType.HOME, title="Home"),
            SelectedPage(url="https://swissbeauty.example.com/products/lip-gloss", page_type=PageType.PRODUCT, title="Lip Gloss"),
            SelectedPage(url="https://swissbeauty.example.com/products/blush", page_type=PageType.PRODUCT, title="Blush"),
        ]

    # --- Scenario 1: Actor returns SUCCEEDED and dataset retrieval succeeds ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_01_actor_succeeds_and_dataset_succeeds(self, mock_client_cls: MagicMock) -> None:
        """When actor finishes with SUCCEEDED and dataset retrieves all items, crawl status is success."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_run = _make_mock_run(status="SUCCEEDED", dataset_id="ds-1")
        mock_client.actor.return_value.call.return_value = mock_run

        dataset_items = [
            {"url": "https://swissbeauty.example.com/", "markdown": "# Swiss Beauty Home\nSubstantive homepage brand description and company background."},
            {"url": "https://swissbeauty.example.com/products/lip-gloss", "markdown": "# Lip Gloss\nUltra shine plumping lip gloss with Vitamin E."},
            {"url": "https://swissbeauty.example.com/products/blush", "markdown": "# Craze Blush\npH-activated color changing blush for cheeks."},
        ]
        mock_client.dataset.return_value.iterate_items.return_value = dataset_items

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            result = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        self.assertEqual(result.status, "success")
        self.assertEqual(result.total_scraped, 3)
        self.assertEqual(result.total_failed, 0)
        self.assertTrue(result.has_usable_content)
        self.assertEqual(result.actor_status, "SUCCEEDED")
        self.assertIsNone(result.error_message)

    # --- Scenario 2: Actor returns SUCCEEDED but dataset retrieval raises an exception ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_02_actor_succeeds_but_dataset_retrieval_raises_exception(self, mock_client_cls: MagicMock) -> None:
        """When actor reaches SUCCEEDED but dataset read throws an exception, actor_status remains SUCCEEDED and error is preserved."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_run = _make_mock_run(status="SUCCEEDED", dataset_id="ds-2")
        mock_client.actor.return_value.call.return_value = mock_run
        mock_client.dataset.return_value.iterate_items.side_effect = RuntimeError("Dataset storage connection reset")

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            result = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.actor_status, "SUCCEEDED")  # Kept as SUCCEEDED, not incorrectly flipped to FAILED
        self.assertEqual(result.total_scraped, 0)
        self.assertFalse(result.has_usable_content)
        self.assertIn("dataset retrieval failed", result.error_message.lower())
        self.assertIn("Dataset storage connection reset", result.error_message)

    # --- Scenario 3: Actor returns TIMED-OUT with recoverable partial items ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_03_actor_times_out_with_recoverable_partial_items(self, mock_client_cls: MagicMock) -> None:
        """When actor times out (e.g. 2/3 crawled), recovered dataset items are preserved as partial results."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_run = _make_mock_run(
            status="TIMED-OUT",
            dataset_id="ds-3",
            status_message="Crawled 2/3 pages, 0 failed requests, desired concurrency 3.",
        )
        mock_client.actor.return_value.call.return_value = mock_run

        dataset_items = [
            {"url": "https://swissbeauty.example.com/", "markdown": "# Swiss Beauty Home\nSubstantive homepage content describing Swiss Beauty cosmetics and skincare products."},
            {"url": "https://swissbeauty.example.com/products/lip-gloss", "markdown": "# Lip Gloss\nUltra shine plumping gloss with Vitamin E."},
        ]
        mock_client.dataset.return_value.iterate_items.return_value = dataset_items

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            result = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.total_scraped, 2)
        self.assertEqual(result.total_failed, 1)
        self.assertTrue(result.has_usable_content)
        self.assertEqual(result.actor_status, "TIMED-OUT")

        failed_blush = next(f for f in result.failed_pages if f.url == "https://swissbeauty.example.com/products/blush")
        self.assertIn("timed out", failed_blush.error_message.lower())

    # --- Scenario 4: Actor returns TIMED-OUT without recoverable items ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_04_actor_times_out_without_recoverable_items(self, mock_client_cls: MagicMock) -> None:
        """When actor times out before saving any dataset items, result is failed and message states no items recovered."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_run = _make_mock_run(status="TIMED-OUT", dataset_id="ds-4", status_message="Timed out during initialization.")
        mock_client.actor.return_value.call.return_value = mock_run
        mock_client.dataset.return_value.iterate_items.return_value = []

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            result = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.total_scraped, 0)
        self.assertFalse(result.has_usable_content)
        self.assertEqual(result.actor_status, "TIMED-OUT")
        self.assertIn("No dataset items were recovered", result.error_message)

    # --- Scenario 5: Actor initially returns nonterminal status and later reaches SUCCEEDED ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_05_actor_initially_nonterminal_later_succeeds(self, mock_client_cls: MagicMock) -> None:
        """When actor call() returns RUNNING, implementation waits on run_client and completes successfully on SUCCEEDED."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # call() returns nonterminal RUNNING
        initial_run = _make_mock_run(run_id="run-5", status="RUNNING", dataset_id="ds-5")
        mock_client.actor.return_value.call.return_value = initial_run

        # wait_for_finish returns terminal SUCCEEDED
        terminal_run = _make_mock_run(run_id="run-5", status="SUCCEEDED", dataset_id="ds-5")
        mock_client.run.return_value.wait_for_finish.return_value = terminal_run

        dataset_items = [
            {"url": "https://swissbeauty.example.com/", "markdown": "# Swiss Beauty Home\nSubstantive homepage brand description and company background."},
            {"url": "https://swissbeauty.example.com/products/lip-gloss", "markdown": "# Lip Gloss\nUltra shine plumping lip gloss with Vitamin E."},
            {"url": "https://swissbeauty.example.com/products/blush", "markdown": "# Craze Blush\npH-activated color changing blush for cheeks."},
        ]
        mock_client.dataset.return_value.iterate_items.return_value = dataset_items

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            result = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        mock_client.run.assert_called_with("run-5")
        mock_client.run.return_value.wait_for_finish.assert_called_once()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.actor_status, "SUCCEEDED")
        self.assertEqual(result.total_scraped, 3)

    # --- Scenario 6: Actor initially returns nonterminal status and later reaches TIMED-OUT with partial results ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_06_actor_initially_nonterminal_later_times_out_with_partial_results(self, mock_client_cls: MagicMock) -> None:
        """When actor call() returns RUNNING, wait_for_finish reaches TIMED-OUT, recovering available items."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        initial_run = _make_mock_run(run_id="run-6", status="RUNNING", dataset_id="ds-6")
        mock_client.actor.return_value.call.return_value = initial_run

        terminal_run = _make_mock_run(run_id="run-6", status="TIMED-OUT", dataset_id="ds-6", status_message="Crawled 1/3 pages.")
        mock_client.run.return_value.wait_for_finish.return_value = terminal_run

        dataset_items = [
            {"url": "https://swissbeauty.example.com/products/lip-gloss", "markdown": "# Lip Gloss\nUltra shine plumping gloss with nourishing Vitamin E."},
        ]
        mock_client.dataset.return_value.iterate_items.return_value = dataset_items

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            result = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        mock_client.run.return_value.wait_for_finish.assert_called_once()
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.actor_status, "TIMED-OUT")
        self.assertEqual(result.total_scraped, 1)
        self.assertTrue(result.has_usable_content)

    # --- Scenario 7: Actor remains nonterminal until overall deadline expires ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_07_actor_remains_nonterminal_until_deadline_expires(self, mock_client_cls: MagicMock) -> None:
        """When actor remains nonterminal (RUNNING) after wait, honest diagnostic is returned without claiming timeout."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        still_running = _make_mock_run(run_id="run-7", status="RUNNING", dataset_id="ds-7")
        mock_client.actor.return_value.call.return_value = still_running
        mock_client.run.return_value.wait_for_finish.return_value = still_running

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            result = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.actor_status, "RUNNING")  # Does not claim TIMED-OUT or FAILED
        self.assertEqual(result.total_scraped, 0)
        self.assertFalse(result.has_usable_content)
        self.assertIn("remained nonterminal ('RUNNING')", result.error_message)

    # --- Scenario 8: Implementation does not start duplicate actor run while active ---
    @patch("backend.app.services.website_content_crawler_service._run_actor_sync")
    async def test_08_no_duplicate_actor_run_while_still_active(self, mock_run_sync: MagicMock) -> None:
        """If actor run remains nonterminal (RUNNING), implementation aborts retry loop without duplicate run."""
        mock_run_sync.return_value = (
            [],
            "run-8",
            "Actor run 'run-8' remained nonterminal ('RUNNING') when operation deadline expired.",
            "RUNNING",
        )

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            result = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        # Crucial: Exactly 1 attempt made, never launches second actor run
        self.assertEqual(mock_run_sync.call_count, 1)
        self.assertEqual(result.actor_status, "RUNNING")
        self.assertEqual(result.status, "failed")

    # --- Scenario 9: Missing dataset ID and empty dataset reported distinctly ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    def test_09_missing_dataset_id_and_empty_dataset_distinct(self, mock_client_cls: MagicMock) -> None:
        """Missing dataset ID and empty dataset produce distinct, identifiable error messages."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # Sub-case A: Missing dataset ID
        mock_client.actor.return_value.call.return_value = _make_mock_run(status="SUCCEEDED", dataset_id=None)
        items_a, _, err_a, _ = _run_actor_sync("dummy", {"startUrls": []}, 60)
        self.assertEqual(items_a, [])
        self.assertIn("returned no default dataset ID in metadata", err_a)

        # Sub-case B: Empty dataset
        mock_client.actor.return_value.call.return_value = _make_mock_run(status="SUCCEEDED", dataset_id="ds-empty-9")
        mock_client.dataset.return_value.iterate_items.return_value = []
        items_b, _, err_b, _ = _run_actor_sync("dummy", {"startUrls": []}, 60)
        self.assertEqual(items_b, [])
        self.assertIn("default dataset 'ds-empty-9' was empty", err_b)

        # Ensure distinct messages
        self.assertNotEqual(err_a, err_b)

    # --- Scenario 10: Genuine FAILED and ABORTED remain distinguishable from timeouts ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_10_failed_and_aborted_distinguishable_from_timeout(self, mock_client_cls: MagicMock) -> None:
        """Actor statuses FAILED and ABORTED are distinguishable from TIMED-OUT in actor_status and message."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # Sub-case A: FAILED
        mock_client.actor.return_value.call.return_value = _make_mock_run(status="FAILED", dataset_id="ds-f", status_message="Out of memory.")
        mock_client.dataset.return_value.iterate_items.return_value = []

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            res_failed = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        self.assertEqual(res_failed.actor_status, "FAILED")
        self.assertIn("status 'FAILED'", res_failed.error_message)

        # Sub-case B: ABORTED
        mock_client.actor.return_value.call.return_value = _make_mock_run(status="ABORTED", dataset_id="ds-a", status_message="Aborted by user.")
        mock_client.dataset.return_value.iterate_items.return_value = []

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            res_aborted = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        self.assertEqual(res_aborted.actor_status, "ABORTED")
        self.assertIn("status 'ABORTED'", res_aborted.error_message)

    # --- Scenario 11: Retry behavior remains bounded and avoids redundant full crawls after partial recovery ---
    @patch("backend.app.services.website_content_crawler_service._run_actor_sync")
    async def test_11_bounded_retries_and_no_redundant_crawl_after_partial(self, mock_run_sync: MagicMock) -> None:
        """When partial items are recovered, loop aborts immediately without redundant attempt 2."""
        partial_items = [
            {"url": "https://swissbeauty.example.com/", "markdown": "# Swiss Beauty Home\nSubstantive content exceeding fifty characters minimum."}
        ]
        mock_run_sync.return_value = (partial_items, "run-11", "Actor run ended with status 'TIMED-OUT'", "TIMED-OUT")

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            result = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        self.assertEqual(mock_run_sync.call_count, 1)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.total_scraped, 1)

    # --- Scenario 12: Final API behavior correctly reflects partial results or insufficient content ---
    def test_12_final_api_behavior_partial_and_insufficient(self) -> None:
        """API returns 200 on partial crawl with sufficient content, and 422 on insufficient content."""
        payload = {
            "business_name_and_description": "Swiss Beauty cosmetics brand.",
            "product_or_service": "Lip gloss and blush",
            "target_audience": "Women seeking affordable makeup",
            "audience_unknown": False,
            "business_website": "https://swissbeauty.example.com",
        }

        # Sub-case A: Partial crawl with sufficient content yields 200
        page1 = ScrapedPage(
            url="https://swissbeauty.example.com/products/lip-gloss",
            page_type=PageType.PRODUCT,
            title="Lip Gloss",
            content="Swiss Beauty Lip Gloss provides long-lasting moisture, shine, and natural tints with Vitamin E.",
        )
        page2 = ScrapedPage(
            url="https://swissbeauty.example.com/",
            page_type=PageType.HOME,
            title="Swiss Beauty Home",
            content="Swiss Beauty is India's leading trendy makeup brand offering affordable, high quality cosmetics.",
        )
        partial_crawl_ok = WebsiteCrawlResult(
            website_url="https://swissbeauty.example.com",
            status="partial",
            scraped_pages=[page1, page2],
            failed_pages=[],
            total_requested=3,
            total_scraped=2,
            total_failed=1,
            has_usable_content=True,
            actor_status="TIMED-OUT",
        )
        overview_ok = BusinessOverviewResult(
            status="success",
            business_overview=BusinessOverview(
                business_name="Swiss Beauty",
                website_url="https://swissbeauty.example.com",
                business_description="Affordable cosmetics brand in India.",
                products_and_services=[
                    ProductServiceItem(
                        name="Lip Gloss",
                        description="Moisturizing lip gloss.",
                        source_url="https://swissbeauty.example.com/products/lip-gloss",
                    )
                ],
                target_audience=TargetAudienceInfo(description="Women seeking cosmetics", is_explicit=True),
                sources_used=["https://swissbeauty.example.com/products/lip-gloss"],
            ),
            website_url="https://swissbeauty.example.com",
            total_pages_analyzed=2,
            total_findings=1,
            execution_time_seconds=2.5,
        )

        with patch("backend.app.agent.nodes.url_discovery_node.discover_website_urls") as p_disc, \
             patch("backend.app.agent.nodes.page_selection_node.select_pages_with_llm") as p_sel, \
             patch("backend.app.agent.nodes.website_crawling_node.crawl_selected_pages", new=AsyncMock(return_value=partial_crawl_ok)), \
             patch("backend.app.agent.nodes.business_overview_node.generate_business_overview", new=AsyncMock(return_value=overview_ok)):

            p_disc.return_value = WebsiteURLDiscoveryResult(
                requested_url="https://swissbeauty.example.com",
                base_url="https://swissbeauty.example.com",
                status="success",
                homepage_status="success",
                candidate_urls=[],
            )
            p_sel.return_value = MagicMock(selected_pages=self.selected_pages)

            res = self.api_client.post("/api/v1/business-overview", json=payload)
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["business_overview"]["business_name"], "Swiss Beauty")

        # Sub-case B: Partial crawl with insufficient content yields 422
        insufficient_overview = BusinessOverviewResult(
            status="insufficient_content",
            business_overview=None,
            website_url="https://swissbeauty.example.com",
            total_pages_analyzed=1,
            total_findings=0,
            error_message="Insufficient readable website content (45 chars across 1 pages; minimum required is 150).",
        )
        with patch("backend.app.agent.nodes.url_discovery_node.discover_website_urls", new=AsyncMock(return_value=p_disc.return_value)), \
             patch("backend.app.agent.nodes.page_selection_node.select_pages_with_llm", new=AsyncMock(return_value=p_sel.return_value)), \
             patch("backend.app.agent.nodes.website_crawling_node.crawl_selected_pages", new=AsyncMock(return_value=partial_crawl_ok)), \
             patch("backend.app.agent.nodes.business_overview_node.generate_business_overview", new=AsyncMock(return_value=insufficient_overview)):

            res_422 = self.api_client.post("/api/v1/business-overview", json=payload)
            self.assertEqual(res_422.status_code, 422)
            self.assertIn("Insufficient readable website content", res_422.json().get("detail", ""))


if __name__ == "__main__":
    unittest.main()
