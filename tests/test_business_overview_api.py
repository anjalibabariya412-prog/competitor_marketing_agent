"""Integration tests for the Business Overview API endpoint (/api/v1/business-overview).

Verifies request validation, LangGraph workflow invocation, response serialization,
and error handling (422, 502, 500) using realistic synthetic mock data without
making real external API calls (OpenAI, Apify, etc.).
"""

from __future__ import annotations

from typing import Any
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app.agent.graph import get_graph
from backend.app.main import app
from backend.app.services.business_overview_service import (
    BusinessOverviewResult,
)
from backend.app.services.website_content_crawler_service import (
    FailedPageScrape,
    WebsiteCrawlResult,
)

from tests.test_agent_workflow_mock import (
    _create_sample_crawl_result,
    _create_sample_discovery_result,
    _create_sample_overview_result,
    _create_sample_selection_result,
)


def _sample_form_payload() -> dict[str, Any]:
    """Return a valid request payload matching BusinessForm input fields."""
    return {
        "business_name_and_description": (
            "Lumina Botanicals is an organic clean-label skincare brand formulating "
            "plant-powered serums, barrier creams, and restorative treatments."
        ),
        "product_or_service": "Clean botanical serums, nourishing moisturizers, and gentle cleansers",
        "target_audience": "Eco-conscious consumers aged 25-50 seeking non-toxic, peptide-rich anti-aging skincare",
        "audience_unknown": False,
        "business_website": "https://www.luminabotanicals.com",
    }


class TestBusinessOverviewAPI(unittest.TestCase):
    """Test suite for the Business Overview FastAPI endpoint."""

    def setUp(self) -> None:
        """Create a FastAPI test client before each test."""
        self.client = TestClient(app)

    def test_health_endpoint_still_operational(self) -> None:
        """Verify that registering the business overview route did not affect /health."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_cors_preflight_options_succeeds_for_frontend_origin(self) -> None:
        """Verify that preflight OPTIONS request returns 200 with CORS headers for Vite origin."""
        headers = {
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, accept",
        }
        response = self.client.options("/api/v1/business-overview", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "http://localhost:5173")
        self.assertIn("POST", response.headers.get("access-control-allow-methods", ""))
        self.assertEqual(response.headers.get("access-control-allow-credentials"), "true")

    def test_cors_preflight_options_succeeds_for_127_0_0_1_origin(self) -> None:
        """Verify that preflight OPTIONS request works for 127.0.0.1:5173 origin."""
        headers = {
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        }
        response = self.client.options("/api/v1/business-overview", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "http://127.0.0.1:5173")

    def test_cors_headers_present_on_post_response(self) -> None:
        """Verify that actual POST responses include CORS headers."""
        headers = {
            "Origin": "http://localhost:5173",
        }
        # Send an invalid payload to verify CORS header presence without invoking heavy workflow
        response = self.client.post("/api/v1/business-overview", json={}, headers=headers)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "http://localhost:5173")
        self.assertEqual(response.headers.get("access-control-allow-credentials"), "true")

    def test_openapi_schema_contains_business_overview_endpoint(self) -> None:
        """Verify that /api/v1/business-overview is registered in FastAPI OpenAPI docs."""
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        paths = schema.get("paths", {})
        self.assertIn("/api/v1/business-overview", paths)
        self.assertIn("post", paths["/api/v1/business-overview"])

    def test_request_validation_rejects_empty_payload(self) -> None:
        """Verify 422 Unprocessable Entity when request body is empty."""
        response = self.client.post("/api/v1/business-overview", json={})
        self.assertEqual(response.status_code, 422)
        errors = response.json().get("detail", [])
        self.assertTrue(len(errors) > 0)

    def test_request_validation_rejects_short_business_description(self) -> None:
        """Verify 422 when business_name_and_description is shorter than 20 characters."""
        payload = {
            "business_name_and_description": "Too short",
            "product_or_service": "Handmade botanical skincare and serums",
            "target_audience": "Skin health enthusiasts",
            "audience_unknown": False,
            "business_website": "https://www.luminabotanicals.com",
        }
        response = self.client.post("/api/v1/business-overview", json=payload)
        self.assertEqual(response.status_code, 422)
        error_str = str(response.json())
        self.assertIn("business_name_and_description", error_str)

    def test_request_validation_rejects_localhost_website(self) -> None:
        """Verify 422 when business_website is a localhost URL."""
        payload = {
            "business_name_and_description": "Lumina Botanicals is an organic clean-label skincare brand.",
            "product_or_service": "Handmade botanical skincare and serums",
            "target_audience": "Skin health enthusiasts",
            "audience_unknown": False,
            "business_website": "http://localhost:8000",
        }
        response = self.client.post("/api/v1/business-overview", json=payload)
        self.assertEqual(response.status_code, 422)
        error_str = str(response.json())
        self.assertIn("Localhost URLs are not allowed", error_str)

    def test_request_validation_rejects_extra_forbidden_fields(self) -> None:
        """Verify 422 when unexpected extra fields such as social_link are provided."""
        payload = {
            "business_name_and_description": "Lumina Botanicals is an organic clean-label skincare brand.",
            "product_or_service": "Handmade botanical skincare and serums",
            "target_audience": "Skin health enthusiasts",
            "audience_unknown": False,
            "business_website": "https://www.luminabotanicals.com",
            "social_link": "https://www.instagram.com/luminabotanicals",
        }
        response = self.client.post("/api/v1/business-overview", json=payload)
        self.assertEqual(response.status_code, 422)
        error_str = str(response.json())
        self.assertIn("extra_forbidden", error_str)

    def test_successful_business_overview_generation(self) -> None:
        """Verify successful 200 response when workflow completes end-to-end with mock data."""
        payload = _sample_form_payload()
        discovery_result, _ = _create_sample_discovery_result()
        selection_result, _ = _create_sample_selection_result()
        crawl_result, _, _ = _create_sample_crawl_result()
        overview_result, overview = _create_sample_overview_result()

        with patch(
            "backend.app.agent.nodes.url_discovery_node.discover_website_urls",
            new=AsyncMock(return_value=discovery_result),
        ), patch(
            "backend.app.agent.nodes.page_selection_node.select_pages_with_llm",
            new=AsyncMock(return_value=selection_result),
        ), patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=crawl_result),
        ), patch(
            "backend.app.agent.nodes.business_overview_node.generate_business_overview",
            new=AsyncMock(return_value=overview_result),
        ):
            response = self.client.post("/api/v1/business-overview", json=payload)

            self.assertEqual(response.status_code, 200)
            data = response.json()

            # Verify the response contains ONLY the 4 required public fields
            expected_keys = {"status", "business_overview", "website_url", "execution_time_seconds"}
            self.assertEqual(set(data.keys()), expected_keys)

            # Verify top-level fields
            self.assertEqual(data.get("status"), "success")
            self.assertEqual(data.get("website_url"), "https://www.luminabotanicals.com")
            self.assertIsInstance(data.get("execution_time_seconds"), (int, float))

            # Verify structured business overview
            overview_data = data.get("business_overview", {})
            self.assertEqual(overview_data.get("business_name"), "Lumina Botanicals")
            self.assertIn("organic skincare", overview_data.get("business_description", "").lower())
            self.assertEqual(len(overview_data.get("products_and_services", [])), 2)
            self.assertIn("target_audience", overview_data)
            self.assertEqual(len(overview_data.get("positioning_and_value_propositions", [])), 2)
            self.assertEqual(len(overview_data.get("information_gaps", [])), 1)
            self.assertEqual(len(overview_data.get("key_findings", [])), 2)

            # Explicitly verify removed fields are NOT present
            self.assertNotIn("workflow_status", data)
            self.assertNotIn("total_pages_analyzed", data)
            self.assertNotIn("total_findings", data)
            self.assertNotIn("model_used", data)
            self.assertNotIn("metadata", data)

    def test_insufficient_content_returns_422(self) -> None:
        """Verify 422 Unprocessable Entity when crawled content is insufficient for analysis."""
        payload = _sample_form_payload()
        discovery_result, _ = _create_sample_discovery_result()
        selection_result, _ = _create_sample_selection_result()

        empty_crawl = WebsiteCrawlResult(
            website_url="https://www.luminabotanicals.com",
            status="empty",
            scraped_pages=[],
            failed_pages=[],
            total_requested=3,
            total_scraped=0,
            total_failed=0,
            has_usable_content=False,
            error_message="Website crawling yielded 0 usable pages.",
        )

        insufficient_overview = BusinessOverviewResult(
            status="insufficient_content",
            business_overview=None,
            website_url="https://www.luminabotanicals.com",
            total_pages_analyzed=0,
            total_findings=0,
            error_message="Insufficient readable website content (0 chars across 0 pages; minimum required is 150).",
        )

        with patch(
            "backend.app.agent.nodes.url_discovery_node.discover_website_urls",
            new=AsyncMock(return_value=discovery_result),
        ), patch(
            "backend.app.agent.nodes.page_selection_node.select_pages_with_llm",
            new=AsyncMock(return_value=selection_result),
        ), patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=empty_crawl),
        ), patch(
            "backend.app.agent.nodes.business_overview_node.generate_business_overview",
            new=AsyncMock(return_value=insufficient_overview),
        ):
            response = self.client.post("/api/v1/business-overview", json=payload)

            self.assertEqual(response.status_code, 422)
            self.assertIn("Insufficient readable website content", response.json().get("detail", ""))

    def test_upstream_crawling_failure_returns_502(self) -> None:
        """Verify 502 Bad Gateway when website crawling fails due to upstream server WAF blocking."""
        payload = _sample_form_payload()
        discovery_result, _ = _create_sample_discovery_result()
        selection_result, selected_pages = _create_sample_selection_result()

        failed_crawl = WebsiteCrawlResult(
            website_url="https://www.luminabotanicals.com",
            status="failed",
            scraped_pages=[],
            failed_pages=[
                FailedPageScrape(
                    url=p.url,
                    error_message="Access denied: Cloudflare challenge",
                    page_type=p.page_type,
                )
                for p in selected_pages
            ],
            total_requested=len(selected_pages),
            total_scraped=0,
            total_failed=len(selected_pages),
            has_usable_content=False,
            error_message="All pages were blocked by target server WAF.",
        )

        with patch(
            "backend.app.agent.nodes.url_discovery_node.discover_website_urls",
            new=AsyncMock(return_value=discovery_result),
        ), patch(
            "backend.app.agent.nodes.page_selection_node.select_pages_with_llm",
            new=AsyncMock(return_value=selection_result),
        ), patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=failed_crawl),
        ):
            response = self.client.post("/api/v1/business-overview", json=payload)

            self.assertEqual(response.status_code, 502)
            self.assertIn("blocked by target server", response.json().get("detail", "").lower())

    def test_unexpected_workflow_exception_returns_500(self) -> None:
        """Verify 500 Internal Server Error when an unexpected exception occurs during execution."""
        payload = _sample_form_payload()

        mock_graph = AsyncMock()
        mock_graph.ainvoke.side_effect = RuntimeError("Critical internal queue timeout")

        with patch("backend.app.api.routes.business_overview.get_graph", return_value=mock_graph):
            # Also override dependency directly on FastAPI app
            app.dependency_overrides[get_graph] = lambda: mock_graph
            try:
                response = self.client.post("/api/v1/business-overview", json=payload)

                # Verify 500 status and that raw exception/stack trace is not leaked
                self.assertEqual(response.status_code, 500)
                self.assertNotIn("Critical internal queue timeout", response.json().get("detail", ""))
                self.assertIn("unexpected error occurred", response.json().get("detail", "").lower())
            finally:
                app.dependency_overrides.pop(get_graph, None)


if __name__ == "__main__":
    unittest.main()
