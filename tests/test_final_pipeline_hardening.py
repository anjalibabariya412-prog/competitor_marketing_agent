"""Unit tests verifying final website pipeline correctness, reliability, and performance fixes.

Covers:
1. Sitemap probing stopped immediately when domain is marked HTTP-blocked.
2. Sitemap probing stopped immediately on robots.txt 401/403.
3. Cross-page content overlap detection and quality evaluation (primary vs weak vs inadequate).
4. Concise specialized pricing/contact page retention as primary evidence.
5. Expanded pricing & offer keyword categorization into SelectionCategory.PRICING_OFFER.
6. Regional context detection cross-referencing user intake vs proxy-induced redirects.
7. Scrape caching refusing inadequate/sparse crawls and enforcing path scoping.
8. Crawler actor input setting 'playwright:chrome' and concurrency bounds on blocked domains.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import backend.app.main  # Ensure canonical import order
import httpx

from backend.app.schemas.enums import PageType
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.business_overview_service import (
    BusinessOverviewResult,
    _format_pages_for_prompt,
    detect_regional_context,
)
from backend.app.services.content_validation import (
    clear_domain_http_blocks,
    compute_text_overlap_ratio,
    evaluate_page_content_quality,
    is_domain_http_blocked,
    mark_domain_http_blocked,
)
from backend.app.services.page_selection_service import (
    SelectionCategory,
    categorize_candidate,
)
from backend.app.services.sitemap_fallback_service import (
    discover_sitemap_urls,
)
from backend.app.services.url_extractor_service import CandidateURL, classify_page_type
from backend.app.services.website_content_crawler_service import (
    WebsiteCrawlResult,
    _build_actor_input,
    _build_crawl_cache_key,
    clear_scrape_cache,
    get_cached_crawl,
    set_cached_crawl,
)


class TestFinalPipelineHardening(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        clear_domain_http_blocks()
        clear_scrape_cache()

    def tearDown(self) -> None:
        clear_domain_http_blocks()
        clear_scrape_cache()

    async def test_sitemap_probes_skipped_upfront_on_blocked_domain(self) -> None:
        """When a domain has already been marked HTTP-blocked, discover_sitemap_urls stops immediately."""
        mark_domain_http_blocked("https://blocked-domain.com", "homepage_http_403")
        self.assertTrue(is_domain_http_blocked("https://blocked-domain.com"))

        with patch("httpx.AsyncClient.get") as mock_get:
            result = await discover_sitemap_urls("https://blocked-domain.com")
            # Must not make ANY HTTP request
            mock_get.assert_not_called()

        self.assertEqual(result.status, "failed")
        self.assertIn("blocked", result.message.lower())
        self.assertEqual(result.candidate_urls, [])
        self.assertEqual(result.sitemaps_checked, [])

    async def test_sitemap_probes_stop_immediately_on_robots_403(self) -> None:
        """When robots.txt returns HTTP 403, sitemap discovery marks domain blocked and does not probe common paths."""
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp
            result = await discover_sitemap_urls("https://unblocked-site.com")

            # Only 1 request to /robots.txt should be executed
            self.assertEqual(mock_get.call_count, 1)
            called_url = mock_get.call_args[0][0]
            self.assertTrue(called_url.endswith("/robots.txt"))

        self.assertEqual(result.status, "failed")
        self.assertIn("blocked", result.message.lower())
        self.assertTrue(is_domain_http_blocked("https://unblocked-site.com"))

    def test_cross_page_overlap_and_quality_evaluation(self) -> None:
        """Duplicate repetitive navigation snippets are flagged as weak while substantive content is primary."""
        common_nav = (
            "Home Eyeglasses Sunglasses Contact Lenses Store Locator Track Order "
            "About Us Privacy Policy Terms and Conditions Cart Checkout Login Register "
            "Free Shipping Cash On Delivery 14 Day Returns Customer Support Toll Free"
        )
        page1 = f"{common_nav} Welcome to our store. Discover premium Italian crafted frames."
        page2 = f"{common_nav} View our computer eyeglasses collection with blue light block lenses."

        # Candidate 2 has mostly common nav overlapping with page 1
        overlap = compute_text_overlap_ratio(page2, [page1])
        self.assertGreater(overlap, 0.60)

        # Evaluate quality with high overlap
        category, reason = evaluate_page_content_quality(
            content=page2,
            page_type=PageType.PRODUCT,
            existing_texts=[page1],
        )
        # Should be weak due to high cross-page overlap
        self.assertEqual(category, "weak")
        self.assertIn("overlap", reason)

        # Distinct new content is primary (> 300 chars)
        page3 = (
            "Our bespoke lens manufacturing facility in Rajasthan utilizes German precision machinery "
            "to grind optical surfaces with 0.01 diopter accuracy. Every lens undergoes 5-stage ultrasonic cleaning "
            "and multi-layer hydrophobic anti-reflective vacuum deposition coating. We supply over 10,000 retail outlets "
            "nationwide with certified optical products backed by comprehensive warranties."
        )
        category3, reason3 = evaluate_page_content_quality(
            content=page3,
            page_type=PageType.ABOUT,
            existing_texts=[page1],
        )
        self.assertEqual(category3, "primary")
        self.assertEqual(reason3, "substantive_content")

    def test_concise_pricing_page_retained_as_primary(self) -> None:
        """Concise pricing pages with explicit numbers/tiers are preserved as primary evidence."""
        pricing_text = (
            "Gold Club Membership: ₹600 for 1 Year. "
            "Includes Buy 1 Get 1 Free on all eyewear frames and 10% extra cashback on lenses."
        )
        category, reason = evaluate_page_content_quality(
            content=pricing_text,
            page_type=PageType.PRICING,
            url="https://example.com/membership",
        )
        self.assertEqual(category, "primary")
        self.assertEqual(reason, "concise_specialized_page")

    def test_expanded_pricing_offer_categorization(self) -> None:
        """Commercial terms like gold membership, club, bogo, coupons map to PRICING_OFFER."""
        c1 = CandidateURL(url="https://example.com/pages/gold-membership", link_text="Gold Membership", page_type=PageType.UNKNOWN)
        c2 = CandidateURL(url="https://example.com/collections/bogo-offers", link_text="BOGO Deals", page_type=PageType.UNKNOWN)
        c3 = CandidateURL(url="https://example.com/club", link_text="VIP Club", page_type=PageType.UNKNOWN)

        self.assertEqual(categorize_candidate(c1), SelectionCategory.PRICING_OFFER)
        self.assertEqual(categorize_candidate(c2), SelectionCategory.PRICING_OFFER)
        self.assertEqual(categorize_candidate(c3), SelectionCategory.PRICING_OFFER)

    def test_detect_regional_context_distinguishes_proxy_redirect_from_intake(self) -> None:
        """Regional detection recognizes proxy-induced /en-us redirects when intake specifies India."""
        scraped_pages = [
            ScrapedPage(url="https://www.lenskart.com/en-us/eyeglasses", content="US Store Eyeglasses in USD", page_type=PageType.PRODUCT),
        ]

        # Case 1: Indian business intake form
        intake_india = {
            "business_name_and_description": "Lenskart optical retail chain in India",
            "target_audience": "Eyewear buyers across India",
            "product_or_service": "Prescription eyeglasses",
        }
        note = detect_regional_context("https://www.lenskart.com", scraped_pages, business_form=intake_india)
        self.assertIsNotNone(note)
        self.assertIn("proxy redirect", note.lower())
        self.assertIn("india", note.lower())
        self.assertIn("inr", note.lower())

        # Case 2: No Indian intake provided (e.g. US company)
        intake_generic = {
            "business_name_and_description": "International eyewear brand",
            "target_audience": "Fashion eyewear shoppers",
        }
        note_us = detect_regional_context("https://www.brand.com", scraped_pages, business_form=intake_generic)
        self.assertIsNotNone(note_us)
        self.assertIn("united states", note_us.lower())

    def test_scrape_cache_refuses_inadequate_crawls_and_scopes_by_url(self) -> None:
        """Crawl results dominated by inadequate pages (<150 chars) are not cached."""
        sparse_result = WebsiteCrawlResult(
            website_url="https://example.com",
            status="success",
            scraped_pages=[
                ScrapedPage(url="https://example.com/p1", content="Short 40 chars", page_type=PageType.PRODUCT),
                ScrapedPage(url="https://example.com/p2", content="Another 50 chars", page_type=PageType.PRODUCT),
            ],
            total_content_characters=90,
            has_usable_content=True,
        )

        set_cached_crawl("https://example.com", sparse_result, ["https://example.com/p1", "https://example.com/p2"])
        cached = get_cached_crawl("https://example.com", ["https://example.com/p1", "https://example.com/p2"])
        self.assertIsNone(cached, "Sparse crawl must not be cached")

        # Substantive crawl is cached
        substantive_result = WebsiteCrawlResult(
            website_url="https://example.com",
            status="success",
            scraped_pages=[
                ScrapedPage(url="https://example.com/p1", content="A" * 400, page_type=PageType.PRODUCT),
                ScrapedPage(url="https://example.com/p2", content="B" * 400, page_type=PageType.PRODUCT),
            ],
            total_content_characters=800,
            has_usable_content=True,
        )
        set_cached_crawl("https://example.com", substantive_result, ["https://example.com/p1", "https://example.com/p2"])
        cached_valid = get_cached_crawl("https://example.com", ["https://example.com/p1", "https://example.com/p2"])
        self.assertIsNotNone(cached_valid, "Substantive crawl should be cached")

        # Different target URLs do not return mismatched cache
        mismatched = get_cached_crawl("https://example.com", ["https://example.com/p3"])
        self.assertIsNone(mismatched, "Different requested URLs must not return mismatched cached crawl")

    def test_crawler_actor_input_uses_chrome_on_blocked_domain(self) -> None:
        """When direct HTTP is blocked for a domain, actor is configured with playwright:chrome directly."""
        mark_domain_http_blocked("https://blocked-host.com")
        urls = [
            "https://blocked-host.com/page1",
            "https://blocked-host.com/page2",
            "https://blocked-host.com/page3",
            "https://blocked-host.com/page4",
            "https://blocked-host.com/page5",
        ]
        actor_input = _build_actor_input(urls)

        # Concurrency should be up to 6 for 5 URLs (not capped at 4)
        self.assertEqual(actor_input["maxConcurrency"], 5)
        # Crawler type should be playwright:chrome directly
        self.assertEqual(actor_input["crawlerType"], "playwright:chrome")
        self.assertEqual(actor_input["maxCrawlDepth"], 0)


if __name__ == "__main__":
    unittest.main()
