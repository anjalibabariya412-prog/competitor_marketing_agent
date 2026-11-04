"""Unit tests for the unified streaming Business Overview SSE endpoint (POST /api/v1/business-overview)."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

import backend.app.main  # Ensure canonical import order
from backend.app.agent.graph import get_graph
from backend.app.main import app
from backend.app.schemas.business_form import BusinessForm
from backend.app.schemas.enums import PageType
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.business_overview_service import (
    BusinessOverview,
    BusinessOverviewResult,
    ProductServiceItem,
    TargetAudienceInfo,
    ValueProposition,
)
from backend.app.services.sitemap_fallback_service import WebsiteURLDiscoveryResult
from backend.app.services.website_content_crawler_service import WebsiteCrawlResult


def _sample_payload() -> dict:
    return {
        "business_name_and_description": "Lumina Botanicals is an organic clean skincare brand formulating peptide-rich serums.",
        "product_or_service": "Organic botanical serums and barrier moisturizers",
        "target_audience": "Eco-conscious skincare consumers",
        "audience_unknown": False,
        "business_website": "https://www.luminabotanicals.com",
    }


def _create_sample_overview() -> tuple[BusinessOverviewResult, BusinessOverview]:
    overview = BusinessOverview(
        business_name="Lumina Botanicals",
        website_url="https://www.luminabotanicals.com",
        business_description="Organic clean skincare brand formulating botanical serums.",
        products_and_services=[
            ProductServiceItem(
                name="Peptide Serum",
                description="Brightening and firming serum with copper peptides.",
                source_url="https://www.luminabotanicals.com/products",
            )
        ],
        target_audience=TargetAudienceInfo(
            description="Eco-conscious skincare consumers",
            is_explicit=True,
            source_url="https://www.luminabotanicals.com/about",
        ),
        positioning_and_value_propositions=[
            ValueProposition(
                statement="Bridges clinical chemistry with pure botanical therapeutics.",
                is_explicit=True,
                source_url="https://www.luminabotanicals.com/about",
            )
        ],
        sources_used=["https://www.luminabotanicals.com/products", "https://www.luminabotanicals.com/about"],
    )
    result = BusinessOverviewResult(
        status="success",
        business_overview=overview,
        website_url="https://www.luminabotanicals.com",
        total_pages_analyzed=2,
        total_findings=2,
        model_used="gpt-6-luna",
        execution_time_seconds=1.5,
    )
    return result, overview


class TestBusinessOverviewStreamAPI(unittest.TestCase):
    """Test suite validating SSE streaming business overview endpoint."""

    def setUp(self) -> None:
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_streaming_endpoint_emits_expected_stages_and_done(self) -> None:
        """Verify the stream emits stages and completes with done event containing full overview."""
        overview_res, bo = _create_sample_overview()

        async def mock_graph_invoke(initial_state: dict):
            cb = initial_state.get("on_progress")
            if cb:
                await cb("visiting_website", "Connecting to website...", {"website_url": "https://www.luminabotanicals.com"})
                await cb("reading_pages", "Reading 3 pages...", {"page_count": 3})
                await cb("extracting_info", "Extracting offerings...", {"page_count": 3})
                await cb("identifying_audience", "Identifying audience...", {})
                await cb("writing_overview", "Writing overview...", {"model": "gpt-6-luna"})

            return {
                "workflow_status": "completed",
                "business_overview": bo,
                "overview_result": overview_res,
                "crawl_result": WebsiteCrawlResult(
                    website_url="https://www.luminabotanicals.com",
                    status="success",
                    has_usable_content=True,
                    scraped_pages=[
                        ScrapedPage(url="https://www.luminabotanicals.com/products", content="Content 1"),
                        ScrapedPage(url="https://www.luminabotanicals.com/about", content="Content 2"),
                    ],
                ),
                "discovery_result": WebsiteURLDiscoveryResult(
                    requested_url="https://www.luminabotanicals.com",
                    base_url="https://www.luminabotanicals.com",
                    status="success",
                    homepage_status="success",
                    candidate_urls=[],
                ),
            }

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(side_effect=mock_graph_invoke)
        app.dependency_overrides[get_graph] = lambda: mock_graph

        response = self.client.post("/api/v1/business-overview", json=_sample_payload())
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers.get("content-type", ""))

        lines = [line.strip() for line in response.text.split("\n") if line.startswith("data: ")]
        events = [json.loads(line[6:]) for line in lines]

        stages = [e.get("stage") for e in events]
        self.assertIn("visiting_website", stages)
        self.assertIn("reading_pages", stages)
        self.assertIn("extracting_info", stages)
        self.assertIn("identifying_audience", stages)
        self.assertIn("writing_overview", stages)
        self.assertIn("done", stages)

        # Check done event payload
        done_event = next(e for e in events if e.get("stage") == "done")
        self.assertEqual(done_event["data"]["status"], "success")
        self.assertEqual(done_event["data"]["business_overview"]["business_name"], "Lumina Botanicals")

    def test_streaming_endpoint_handles_scrape_failure(self) -> None:
        """Verify stream emits scrape_failed event when crawling fails with 0 content."""
        async def mock_failed_crawl(initial_state: dict):
            cb = initial_state.get("on_progress")
            if cb:
                await cb("visiting_website", "Connecting...", {})
            return {
                "workflow_status": "failed",
                "business_overview": None,
                "overview_result": None,
                "crawl_result": WebsiteCrawlResult(
                    website_url="https://www.luminabotanicals.com",
                    status="failed",
                    has_usable_content=False,
                    error_message="All pages were blocked by target server WAF.",
                ),
                "error_message": "All pages were blocked by target server WAF.",
            }

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(side_effect=mock_failed_crawl)
        app.dependency_overrides[get_graph] = lambda: mock_graph

        response = self.client.post("/api/v1/business-overview", json=_sample_payload())
        self.assertEqual(response.status_code, 200)

        lines = [line.strip() for line in response.text.split("\n") if line.startswith("data: ")]
        events = [json.loads(line[6:]) for line in lines]
        stages = [e.get("stage") for e in events]

        self.assertIn("scrape_failed", stages)
        failed_event = next(e for e in events if e.get("stage") == "scrape_failed")
        self.assertIn("blocked", failed_event["message"].lower())

    def test_old_stream_endpoint_is_removed(self) -> None:
        """Verify POST /api/v1/business-overview/stream is removed and returns 404."""
        response = self.client.post("/api/v1/business-overview/stream", json=_sample_payload())
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
