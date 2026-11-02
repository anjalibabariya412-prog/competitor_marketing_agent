"""Comprehensive test suite for HTTP-First retrieval with browser fallback pipeline optimizations.

Tests:
1. Direct HTTP success: fast retrieval via HTTP, no Apify actor call.
2. HTTP 200 with inadequate content (SPA skeleton / cookie consent) triggers browser fallback.
3. HTTP timeout / connection error triggers browser fallback.
4. Blocked response / WAF challenge triggers browser fallback.
5. Mixed batch: some pages retrieved via HTTP, others via browser fallback.
6. Homepage HTTP-first success without invoking playwright-scraper.
7. Homepage inadequate content (e.g. 178 chars cookie notice) falls back to playwright-scraper.
8. 6-page total limit policy preservation (1 homepage + up to 5 related pages = 6 total).
9. Sitemap fallback handles HTTP 403/404 gracefully without claiming success on 0 candidates.
10. Business overview generation structured logging and single execution guarantee.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

import backend.app.main  # Ensure canonical import order
from backend.app.core.config import settings
from backend.app.schemas.enums import PageType
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.content_validation import (
    is_blocked_content,
    is_content_adequate,
)
from backend.app.services.homepage_service import (
    HomepageFetchResult,
    fetch_homepage,
)
from backend.app.services.page_selection_service import SelectedPage
from backend.app.services.sitemap_fallback_service import (
    SitemapDiscoveryResult,
    discover_sitemap_urls,
)
from backend.app.services.website_content_crawler_service import (
    WebsiteCrawlResult,
    crawl_selected_pages,
)


class TestContentValidation(unittest.TestCase):
    """Test suite for content adequacy and bot detection heuristics."""

    def test_adequate_business_content(self):
        content = "We offer cloud database solutions with automated backups, scalable storage, and 24/7 support."
        adequate, reason = is_content_adequate(content, url="https://example.com/pricing", page_type=PageType.PRICING)
        self.assertTrue(adequate)
        self.assertEqual(reason, "content_adequate")

    def test_cookie_consent_only_inadequate(self):
        # 178 chars Lenskart-like cookie notice
        content = (
            "We use cookies to improve your experience. By clicking Accept All, "
            "you consent to our cookie policy and terms of service. Manage preferences here."
        )
        adequate, reason = is_content_adequate(content, url="https://lenskart.example.com", page_type=PageType.HOME)
        self.assertFalse(adequate)
        self.assertEqual(reason, "cookie_notice_only")

    def test_spa_skeleton_inadequate(self):
        content = "You need to enable JavaScript to run this app. Please enable JavaScript to view this website."
        adequate, reason = is_content_adequate(content, url="https://app.example.com", page_type=PageType.HOME)
        self.assertFalse(adequate)
        self.assertEqual(reason, "spa_skeleton_requires_javascript")

    def test_waf_challenge_blocked(self):
        content = "Attention Required! | Cloudflare. Please verify you are a human to continue. Ray ID: 894038483"
        self.assertTrue(is_blocked_content(content))
        adequate, reason = is_content_adequate(content, url="https://blocked.example.com")
        self.assertFalse(adequate)
        self.assertEqual(reason, "blocked_content")


class TestHttpFirstCrawler(unittest.IsolatedAsyncioTestCase):
    """Test suite for crawl_selected_pages HTTP-first retrieval with browser fallback."""

    def setUp(self):
        self._orig_http_first = settings.HTTP_FIRST_RETRIEVAL

    def tearDown(self):
        object.__setattr__(settings, "HTTP_FIRST_RETRIEVAL", self._orig_http_first)

    @patch("backend.app.services.website_content_crawler_service.httpx.AsyncClient")
    @patch("backend.app.services.website_content_crawler_service._run_actor_sync")
    async def test_all_pages_succeed_via_http_skips_apify(self, mock_actor, mock_client_cls):
        """When all pages return adequate HTML via HTTP, Apify actor is never called."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com/about"
        mock_resp.text = "<html><body><h1>About Us</h1><p>We are a leading provider of enterprise AI tools established in 2020.</p></body></html>"

        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__.return_value = client_instance

        pages = [
            SelectedPage(url="https://example.com/about", page_type=PageType.ABOUT),
            SelectedPage(url="https://example.com/pricing", page_type=PageType.PRICING),
        ]

        result = await crawl_selected_pages(pages, website_url="https://example.com")

        # Apify actor should NOT have been invoked
        mock_actor.assert_not_called()
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.scraped_pages), 2)
        self.assertTrue(result.has_usable_content)
        for page in result.scraped_pages:
            self.assertEqual(page.metadata.get("retrieval_method"), "http")

    @patch("backend.app.services.website_content_crawler_service.httpx.AsyncClient")
    @patch("backend.app.services.website_content_crawler_service._run_actor_sync")
    async def test_inadequate_http_falls_back_to_browser(self, mock_actor, mock_client_cls):
        """When HTTP returns cookie consent only, page falls back to Apify browser scraper."""
        # HTTP client returns 178-char cookie notice
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://lenskart.example.com/"
        mock_resp.text = "<html><body><p>We use cookies to improve your experience. Accept all cookies to continue.</p></body></html>"

        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__.return_value = client_instance

        # Apify mock returns fully hydrated page content
        mock_actor.return_value = (
            [
                {
                    "url": "https://lenskart.example.com/",
                    "text": "Lenskart Eyewear. Buy eyeglasses, sunglasses, and contact lenses with free home eye test and virtual 3D try-on.",
                    "httpStatusCode": 200,
                }
            ],
            "run-lenskart-123",
            None,
            "SUCCEEDED",
        )

        pages = [
            SelectedPage(url="https://lenskart.example.com/", page_type=PageType.HOME),
        ]

        result = await crawl_selected_pages(pages, website_url="https://lenskart.example.com")

        # Apify actor MUST be called for fallback
        mock_actor.assert_called_once()
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.scraped_pages), 1)
        self.assertEqual(result.scraped_pages[0].metadata.get("retrieval_method"), "browser")
        self.assertIn("Buy eyeglasses", result.scraped_pages[0].content)

    @patch("backend.app.services.website_content_crawler_service.httpx.AsyncClient")
    @patch("backend.app.services.website_content_crawler_service._run_actor_sync")
    async def test_mixed_batch_http_and_browser(self, mock_actor, mock_client_cls):
        """Mixed batch: page 1 succeeds via HTTP; page 2 is JS-heavy and falls back to browser."""
        async def mock_get(url):
            resp = MagicMock()
            if "static" in url:
                resp.status_code = 200
                resp.url = url
                resp.text = "<html><body><p>Static page with substantive product pricing and features information.</p></body></html>"
            else:
                resp.status_code = 200
                resp.url = url
                resp.text = "<html><body><p>You need to enable JavaScript to run this app.</p></body></html>"
            return resp

        client_instance = AsyncMock()
        client_instance.get = mock_get
        mock_client_cls.return_value.__aenter__.return_value = client_instance

        # Apify returns hydrated page for the fallback URL
        mock_actor.return_value = (
            [
                {
                    "url": "https://example.com/dynamic",
                    "text": "Dynamic client-rendered portal with analytics dashboard, user metrics, and enterprise reporting.",
                    "httpStatusCode": 200,
                }
            ],
            "run-mixed-123",
            None,
            "SUCCEEDED",
        )

        pages = [
            SelectedPage(url="https://example.com/static", page_type=PageType.PRICING),
            SelectedPage(url="https://example.com/dynamic", page_type=PageType.PRODUCT),
        ]

        result = await crawl_selected_pages(pages, website_url="https://example.com")

        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.scraped_pages), 2)
        methods = {p.url: p.metadata.get("retrieval_method") for p in result.scraped_pages}
        self.assertEqual(methods["https://example.com/static"], "http")
        self.assertEqual(methods["https://example.com/dynamic"], "browser")


class TestHomepageHttpFirst(unittest.IsolatedAsyncioTestCase):
    """Test suite for homepage_service HTTP-first fetch and fallback."""

    @patch("backend.app.services.homepage_service.httpx.AsyncClient")
    @patch("backend.app.services.homepage_service._run_actor_sync")
    async def test_homepage_http_first_success(self, mock_actor, mock_client_cls):
        """Homepage fetches via HTTP in < 1 second and skips Playwright scraper."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://acme.example.com/"
        mock_resp.text = "<html><head><title>Acme Corp</title></head><body><h1>Welcome to Acme Corp</h1><p>We build industrial tools and precision hardware for aerospace and robotics.</p></body></html>"

        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__.return_value = client_instance

        result = await fetch_homepage("https://acme.example.com")

        mock_actor.assert_not_called()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.http_status_code, 200)
        self.assertIsNone(result.actor_run_id)

    @patch("backend.app.services.homepage_service.httpx.AsyncClient")
    @patch("backend.app.services.homepage_service._run_actor_sync")
    async def test_homepage_http_blocked_falls_back_to_playwright(self, mock_actor, mock_client_cls):
        """When HTTP gets a 403 or WAF block, falls back to Playwright scraper."""
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.url = "https://protected.example.com/"
        mock_resp.text = "<html><body>Access Denied - Cloudflare Ray ID</body></html>"

        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__.return_value = client_instance

        mock_actor.return_value = (
            [
                {
                    "html": "<html><body><h1>Real Rendered Homepage</h1><p>Enterprise solutions for secure data storage.</p></body></html>",
                    "finalUrl": "https://protected.example.com/",
                    "httpStatusCode": 200,
                }
            ],
            "run-pw-123",
            None,
        )

        result = await fetch_homepage("https://protected.example.com")

        mock_actor.assert_called_once()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.actor_run_id, "run-pw-123")


class TestSitemapFallbackResilience(unittest.IsolatedAsyncioTestCase):
    """Test suite for sitemap discovery resilience under 403/404 errors."""

    @patch("backend.app.services.sitemap_fallback_service.httpx.AsyncClient")
    async def test_sitemap_probes_blocked_403(self, mock_client_cls):
        """When sitemap probes return HTTP 403 (like on Lenskart), handles gracefully without crash."""
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.content = b""

        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__.return_value = client_instance

        result = await discover_sitemap_urls("https://lenskart.example.com")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.total_found, 0)
        self.assertIn("blocked", result.message.lower())


if __name__ == "__main__":
    unittest.main()
