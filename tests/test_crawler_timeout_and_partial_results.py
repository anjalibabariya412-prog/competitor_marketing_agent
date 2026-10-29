"""Offline regression tests for Apify crawler timeout and partial-result handling.

Covers all 12 scenarios required:
1. Actor succeeds and returns all expected pages.
2. Actor times out but its dataset contains enough valid partial results.
3. Actor times out and the dataset contains no items.
4. Actor times out and the dataset contains only empty or invalid pages.
5. Partial results satisfy minimum-content requirement and Business Overview proceeds.
6. Partial results do not satisfy minimum-content requirement and returns clear error.
7. Dataset retrieval fails after a timeout.
8. Actor metadata is missing a dataset identifier.
9. Log-streaming separation prevents log exceptions from failing actor execution.
10. Genuine actor failure remains distinguishable from a timeout.
11. Transient failures follow retry policy without triggering unnecessary repeated crawls.
12. Final API behavior correctly reflects partial results or insufficient content.
"""

from __future__ import annotations

from typing import Any
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

import backend.app.main  # Ensure canonical import order
from backend.app.agent.graph import get_graph
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
    # Dict access support
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
    """Test suite validating resilient crawler timeout handling and partial result recovery."""

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

    # --- Scenario 1: Actor succeeds and returns all expected pages ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_01_actor_succeeds_returns_all_pages(self, mock_client_cls: MagicMock) -> None:
        """When actor finishes with SUCCEEDED, all pages are returned with success status."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_run = _make_mock_run(status="SUCCEEDED", dataset_id="ds-1")
        mock_client.actor.return_value.call.return_value = mock_run

        dataset_items = [
            {"url": "https://swissbeauty.example.com/", "markdown": "# Swiss Beauty Home\nSubstantive homepage brand description."},
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
        self.assertEqual(len(result.scraped_pages), 3)

    # --- Scenario 2: Actor times out but dataset contains enough valid partial results ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_02_actor_times_out_with_valid_partial_results(self, mock_client_cls: MagicMock) -> None:
        """When actor times out (e.g. 2/3 crawled), recovered dataset items are preserved as partial results."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_run = _make_mock_run(
            status="TIMED-OUT",
            dataset_id="ds-2",
            status_message="Crawled 2/3 pages, 0 failed requests, desired concurrency 3.",
        )
        mock_client.actor.return_value.call.return_value = mock_run

        # Only 2 of 3 crawled before timeout
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

        # Missing 3rd page must be in failed_pages with accurate reason
        failed_urls = [f.url for f in result.failed_pages]
        self.assertIn("https://swissbeauty.example.com/products/blush", failed_urls)
        failed_blush = next(f for f in result.failed_pages if f.url == "https://swissbeauty.example.com/products/blush")
        self.assertIn("timed out", failed_blush.error_message.lower())

    # --- Scenario 3: Actor times out and dataset contains no items ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_03_actor_times_out_with_zero_dataset_items(self, mock_client_cls: MagicMock) -> None:
        """When actor times out before saving any dataset items, result is failed with zero content."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_run = _make_mock_run(status="TIMED-OUT", dataset_id="ds-3", status_message="Timed out during initialization.")
        mock_client.actor.return_value.call.return_value = mock_run
        mock_client.dataset.return_value.iterate_items.return_value = []

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            result = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.total_scraped, 0)
        self.assertEqual(result.total_failed, 3)
        self.assertFalse(result.has_usable_content)
        self.assertEqual(result.actor_status, "TIMED-OUT")
        self.assertIn("TIMED-OUT", result.error_message)

    # --- Scenario 4: Actor times out and dataset contains only empty/invalid pages ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_04_actor_times_out_with_empty_or_stub_pages(self, mock_client_cls: MagicMock) -> None:
        """When actor times out and dataset items have < 50 chars, no usable pages are returned."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_run = _make_mock_run(status="TIMED-OUT", dataset_id="ds-4")
        mock_client.actor.return_value.call.return_value = mock_run

        dataset_items = [
            {"url": "https://swissbeauty.example.com/", "markdown": "Short stub"},
            {"url": "https://swissbeauty.example.com/products/lip-gloss", "markdown": "Too short"},
        ]
        mock_client.dataset.return_value.iterate_items.return_value = dataset_items

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            result = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        self.assertEqual(result.total_scraped, 0)
        self.assertFalse(result.has_usable_content)
        self.assertEqual(result.actor_status, "TIMED-OUT")
        self.assertEqual(result.status, "empty")

    # --- Scenario 5: Partial results satisfy minimum-content requirement and Business Overview proceeds ---
    async def test_05_partial_results_satisfy_min_content_workflow_proceeds(self) -> None:
        """Partial crawl with >= 150 total chars proceeds to business_overview_node successfully."""
        scraped_page = ScrapedPage(
            url="https://swissbeauty.example.com/products/lip-gloss",
            page_type=PageType.PRODUCT,
            title="Lip Gloss Collection",
            content="Swiss Beauty high gloss lip tint offering long lasting nourishment, hydrating oils, and vivid colors for daily wear.",
        )
        partial_crawl = WebsiteCrawlResult(
            website_url="https://swissbeauty.example.com",
            status="partial",
            scraped_pages=[scraped_page],
            failed_pages=[
                FailedPageScrape(
                    url="https://swissbeauty.example.com/products/blush",
                    error_message="Page not scraped: crawler run timed out before completion.",
                    page_type=PageType.PRODUCT,
                )
            ],
            total_requested=2,
            total_scraped=1,
            total_failed=1,
            has_usable_content=True,
            actor_status="TIMED-OUT",
        )

        state: AgentState = {
            "business_form": self.sample_form,
            "selected_pages": self.selected_pages[:2],
        }

        with patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=partial_crawl),
        ):
            crawling_update = await website_crawling_node(state)

        self.assertEqual(crawling_update["workflow_status"], "running")
        self.assertEqual(len(crawling_update["scraped_pages"]), 1)
        self.assertTrue(any("Actor timed out; 1 partial pages recovered" in err for err in crawling_update["errors"]))

    # --- Scenario 6: Partial results do not satisfy minimum-content requirement ---
    async def test_06_partial_results_insufficient_content_returns_clear_error(self) -> None:
        """When partial crawl yields < 150 chars total, business_overview_node reports insufficient_content."""
        scraped_page = ScrapedPage(
            url="https://swissbeauty.example.com/products/lip-gloss",
            page_type=PageType.PRODUCT,
            title="Short Gloss",
            content="Swiss Beauty lip gloss in nude shade.",  # 37 chars < 150 MIN_TOTAL_CONTENT_CHARS
        )
        partial_crawl = WebsiteCrawlResult(
            website_url="https://swissbeauty.example.com",
            status="partial",
            scraped_pages=[scraped_page],
            failed_pages=[],
            total_requested=2,
            total_scraped=1,
            total_failed=1,
            has_usable_content=True,
            actor_status="TIMED-OUT",
        )

        state: AgentState = {
            "business_form": self.sample_form,
            "crawl_result": partial_crawl,
            "scraped_pages": [scraped_page],
        }

        overview_update = await business_overview_node(state)
        self.assertEqual(overview_update["workflow_status"], "failed")
        self.assertEqual(overview_update["overview_result"].status, "insufficient_content")
        self.assertIn("Insufficient readable website content", overview_update["error_message"])

    # --- Scenario 7: Dataset retrieval fails after a timeout ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_07_dataset_retrieval_exception_after_timeout_handled_safely(self, mock_client_cls: MagicMock) -> None:
        """When iterate_items() raises an exception after timeout, error is caught and crawl fails cleanly."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_run = _make_mock_run(status="TIMED-OUT", dataset_id="ds-7")
        mock_client.actor.return_value.call.return_value = mock_run
        mock_client.dataset.return_value.iterate_items.side_effect = RuntimeError("Dataset read connection reset")

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            result = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.total_scraped, 0)
        self.assertFalse(result.has_usable_content)
        self.assertEqual(result.actor_status, "TIMED-OUT")

    # --- Scenario 8: Actor metadata is missing a dataset identifier ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_08_actor_metadata_missing_dataset_id(self, mock_client_cls: MagicMock) -> None:
        """When actor run returns default_dataset_id=None, it fails gracefully without raising AttributeError."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_run = _make_mock_run(status="TIMED-OUT", dataset_id=None)
        mock_client.actor.return_value.call.return_value = mock_run

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            result = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.total_scraped, 0)
        self.assertFalse(result.has_usable_content)
        self.assertIn("No default dataset ID", result.error_message)

    # --- Scenario 9: Log-streaming separation prevents log exceptions from failing crawl ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    def test_09_run_actor_sync_disables_background_log_streaming(self, mock_client_cls: MagicMock) -> None:
        """Verify that _run_actor_sync passes logger=None to call() to disable streaming background threads."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_run = _make_mock_run(status="SUCCEEDED", dataset_id="ds-9")
        mock_client.actor.return_value.call.return_value = mock_run
        mock_client.dataset.return_value.iterate_items.return_value = []

        _run_actor_sync("dummy_token", {"startUrls": [{"url": "https://swissbeauty.example.com"}]}, 120)

        # Confirm call() was passed logger=None
        _, kwargs = mock_client.actor.return_value.call.call_args
        self.assertIn("logger", kwargs)
        self.assertIsNone(kwargs["logger"])

    # --- Scenario 10: Genuine actor failure remains distinguishable from a timeout ---
    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_10_genuine_actor_failure_distinguishable_from_timeout(self, mock_client_cls: MagicMock) -> None:
        """Actor ending with status FAILED is distinguishable from TIMED-OUT in metadata and message."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_run = _make_mock_run(status="FAILED", dataset_id="ds-10", status_message="Actor container out of memory.")
        mock_client.actor.return_value.call.return_value = mock_run
        mock_client.dataset.return_value.iterate_items.return_value = []

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            result = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.actor_status, "FAILED")
        self.assertNotEqual(result.actor_status, "TIMED-OUT")
        self.assertIn("Actor run ended with status 'FAILED'", result.error_message)

    # --- Scenario 11: Transient failures follow retry policy without repeated crawls ---
    @patch("backend.app.services.website_content_crawler_service._run_actor_sync")
    async def test_11_retry_policy_avoids_repeated_crawls_on_partial_recovery(self, mock_run_sync: MagicMock) -> None:
        """If partial items are recovered from a timed-out run, do not retry a full crawl."""
        partial_items = [
            {"url": "https://swissbeauty.example.com/", "markdown": "# Swiss Beauty\nHome content exceeding fifty characters minimum."}
        ]
        # Return partial items on attempt 1 with TIMED-OUT status
        mock_run_sync.return_value = (partial_items, "run-11", "Actor run ended with status 'TIMED-OUT'", "TIMED-OUT")

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-123"

            result = await crawl_selected_pages(self.selected_pages, website_url="https://swissbeauty.example.com")

        # Must execute only 1 attempt and break immediately
        self.assertEqual(mock_run_sync.call_count, 1)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.total_scraped, 1)

    # --- Scenario 12: Final API behavior correctly reflects partial results or insufficient content ---
    def test_12_final_api_behavior_for_partial_and_insufficient_content(self) -> None:
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
