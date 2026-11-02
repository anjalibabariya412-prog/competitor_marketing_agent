"""Unit tests for website content crawler diagnostic logging and timing metrics."""

import logging
import unittest
from unittest.mock import MagicMock, patch

import backend.app.main  # Ensure canonical import order
from backend.app.schemas.enums import PageType
from backend.app.services.page_selection_service import SelectedPage
from backend.app.services.website_content_crawler_service import (
    _PageDiagnostic,
    _categorize_error,
    _extract_item_duration,
    _log_crawler_diagnostics,
    crawl_selected_pages,
)


class TestCrawlerDiagnosticLogging(unittest.IsolatedAsyncioTestCase):
    """Test suite validating per-page diagnostics and aggregate summary logging."""

    def test_extract_item_duration_milliseconds(self) -> None:
        """Verify extraction from millisecond fields converts properly to seconds."""
        item1 = {"crawl": {"requestHandlerDurationMillis": 3456}}
        self.assertEqual(_extract_item_duration(item1), 3.46)

        item2 = {"durationMillis": 1200}
        self.assertEqual(_extract_item_duration(item2), 1.20)

        item3 = {"crawl": {"crawlDurationMillis": "5000"}}
        self.assertEqual(_extract_item_duration(item3), 5.0)

    def test_extract_item_duration_seconds(self) -> None:
        """Verify extraction from direct seconds fields."""
        item = {"crawl": {"executionTime": 4.75}}
        self.assertEqual(_extract_item_duration(item), 4.75)

    def test_extract_item_duration_iso_timestamps(self) -> None:
        """Verify extraction from ISO start and finish timestamps."""
        item = {
            "crawl": {
                "requestStartedAt": "2026-11-02T10:00:00.000Z",
                "requestFinishedAt": "2026-11-02T10:00:03.250Z",
            }
        }
        self.assertEqual(_extract_item_duration(item), 3.25)

    def test_extract_item_duration_returns_none_when_unavailable(self) -> None:
        """Verify None is returned when timing fields are not present (never guessed)."""
        item = {"url": "https://example.com/about", "markdown": "# About"}
        self.assertIsNone(_extract_item_duration(item))
        self.assertIsNone(_extract_item_duration({}))
        self.assertIsNone(_extract_item_duration(None))  # type: ignore[arg-type]

    def test_categorize_error_various_scenarios(self) -> None:
        """Verify error message and status code categorization."""
        cat, reason = _categorize_error("HTTP 404 not found", status_code=404)
        self.assertEqual(cat, "HTTP_NOT_FOUND")

        cat, reason = _categorize_error("Forbidden", status_code=403)
        self.assertEqual(cat, "HTTP_FORBIDDEN")

        cat, reason = _categorize_error("Cloudflare challenge block detected")
        self.assertEqual(cat, "ACCESS_DENIED")

        cat, reason = _categorize_error("Extracted content is too short or empty (12 characters).")
        self.assertEqual(cat, "EMPTY_CONTENT")

        cat, reason = _categorize_error("crawler run timed out before completion.")
        self.assertEqual(cat, "ACTOR_TIMEOUT")

        cat, reason = _categorize_error("Skipped social media URL")
        self.assertEqual(cat, "DISALLOWED_DOMAIN")

        cat, reason = _categorize_error("Page was not returned in the crawler dataset output.")
        self.assertEqual(cat, "NOT_RETURNED")

    def test_log_crawler_diagnostics_formats_correctly(self) -> None:
        """Verify _log_crawler_diagnostics outputs structured logs and aggregate summary."""
        diagnostics = [
            _PageDiagnostic(
                requested_url="https://example.com/",
                final_url="https://example.com/",
                status="SUCCESS",
                http_status=200,
                duration_seconds=2.45,
                char_count=15200,
                is_truncated=False,
            ),
            _PageDiagnostic(
                requested_url="http://example.com/products",
                final_url="https://example.com/products",
                status="SUCCESS",
                http_status=200,
                duration_seconds=None,  # Timing unavailable
                char_count=8500,
                is_truncated=True,
            ),
            _PageDiagnostic(
                requested_url="https://example.com/private",
                final_url="https://example.com/private",
                status="FAILED",
                http_status=403,
                duration_seconds=None,
                char_count=0,
                error_category="HTTP_FORBIDDEN",
                error_reason="HTTP 403 Forbidden/Unauthorized",
            ),
        ]

        with self.assertLogs("backend.app.services.website_content_crawler_service", level="INFO") as cm:
            _log_crawler_diagnostics(
                website_url="https://example.com",
                overall_status="partial",
                actor_status="SUCCEEDED",
                total_requested=3,
                total_scraped=2,
                total_failed=1,
                wall_clock_seconds=15.2,
                diagnostics=diagnostics,
                actor_run_id="run-test-xyz",
            )

        log_output = "\n".join(cm.output)
        self.assertIn("CRAWLER PER-PAGE DIAGNOSTICS: https://example.com", log_output)
        self.assertIn("run-test-xyz", log_output)
        self.assertIn("SUCCESS | url='https://example.com/'", log_output)
        self.assertIn("duration=2.45s", log_output)
        self.assertIn("content=15200 chars", log_output)
        self.assertIn("redirect_to='https://example.com/products'", log_output)
        self.assertIn("duration=unavailable (not reported by actor dataset)", log_output)
        self.assertIn("[truncated]", log_output)
        self.assertIn("FAILED  | url='https://example.com/private'", log_output)
        self.assertIn("error=[HTTP_FORBIDDEN]", log_output)

        self.assertIn("CRAWLER AGGREGATE SUMMARY: https://example.com", log_output)
        self.assertIn("Total Requested URLs: 3", log_output)
        self.assertIn("Successful Pages: 2", log_output)
        self.assertIn("Failed Pages: 1", log_output)
        self.assertIn("Total Extracted Content: 23,700 characters across 2 successful page(s)", log_output)
        self.assertIn("Crawler Wall-Clock Duration: 15.20s", log_output)
        self.assertIn("measured for 1/3 page(s)", log_output)

    @patch("backend.app.services.website_content_crawler_service.ApifyClient")
    async def test_crawl_selected_pages_end_to_end_logging(self, mock_client_cls: MagicMock) -> None:
        """Verify crawl_selected_pages produces per-page logs and aggregate summary."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_run = MagicMock()
        mock_run.id = "run-e2e-123"
        mock_run.status = "SUCCEEDED"
        mock_run.default_dataset_id = "ds-e2e-456"
        mock_run.get = lambda k, default=None: {
            "id": "run-e2e-123",
            "status": "SUCCEEDED",
            "default_dataset_id": "ds-e2e-456",
        }.get(k, default)
        mock_client.actor.return_value.call.return_value = mock_run

        dataset_items = [
            {
                "url": "https://brand.example.com/",
                "loadedUrl": "https://brand.example.com/",
                "markdown": "# Brand Home\nSubstantive content about the skincare brand formulations.",
                "statusCode": 200,
                "crawl": {"requestHandlerDurationMillis": 1850},
            },
            {
                "url": "https://brand.example.com/products",
                "loadedUrl": "https://brand.example.com/all-products",
                "markdown": "# Products\nOur range of peptide and ceramide cleansers.",
                "statusCode": 200,
                # No duration reported
            },
        ]
        mock_client.dataset.return_value.iterate_items.return_value = dataset_items

        pages = [
            SelectedPage(url="https://brand.example.com/", page_type=PageType.HOME),
            SelectedPage(url="https://brand.example.com/products", page_type=PageType.PRODUCT),
        ]

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "token-xyz"
            with self.assertLogs("backend.app.services.website_content_crawler_service", level="INFO") as cm:
                res = await crawl_selected_pages(pages, website_url="https://brand.example.com")

        self.assertEqual(res.status, "success")
        self.assertEqual(res.total_scraped, 2)
        self.assertEqual(res.total_failed, 0)

        log_output = "\n".join(cm.output)
        self.assertIn("CRAWLER PER-PAGE DIAGNOSTICS: https://brand.example.com", log_output)
        self.assertIn("duration=1.85s", log_output)
        self.assertIn("redirect_to='https://brand.example.com/all-products'", log_output)
        self.assertIn("duration=unavailable (not reported by actor dataset)", log_output)
        self.assertIn("CRAWLER AGGREGATE SUMMARY: https://brand.example.com", log_output)
        self.assertIn("Total Requested URLs: 2", log_output)
        self.assertIn("Successful Pages: 2", log_output)


if __name__ == "__main__":
    unittest.main()
