"""Tests for Phase 3: Smarter page selection and fallback candidate crawling."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import backend.app.main  # Ensure standard import ordering
from backend.app.core.config import settings
from backend.app.schemas.enums import PageType
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.url_extractor_service import CandidateURL
from backend.app.services.page_selection_service import (
    PageSelectionResult,
    SelectedPage,
    select_pages_deterministic,
)
from backend.app.services.website_content_crawler_service import (
    FailedPageScrape,
    WebsiteCrawlResult,
)
from backend.app.agent.nodes.website_crawling_node import website_crawling_node


class TestPhase3PageSelection(unittest.IsolatedAsyncioTestCase):
    """Test suite for Phase 3 improvements."""

    def test_low_value_policy_urls_penalized_when_pool_exceeds_budget(self) -> None:
        """When total candidates > max_pages, policy/terms/privacy URLs are penalized and ranked last."""
        candidates = [
            CandidateURL(url="https://example.com/about", page_type=PageType.ABOUT, link_text="About Us", source_section="nav"),
            CandidateURL(url="https://example.com/privacy-policy", page_type=PageType.POLICY, link_text="Privacy Policy", source_section="footer"),
            CandidateURL(url="https://example.com/products/widget-a", page_type=PageType.PRODUCT, link_text="Widget A", source_section="content"),
            CandidateURL(url="https://example.com/products/widget-b", page_type=PageType.PRODUCT, link_text="Widget B", source_section="content"),
            CandidateURL(url="https://example.com/terms-and-conditions", page_type=PageType.POLICY, link_text="Terms", source_section="footer"),
            CandidateURL(url="https://example.com/pricing", page_type=PageType.PRICING, link_text="Pricing", source_section="header"),
            CandidateURL(url="https://example.com/contact", page_type=PageType.CONTACT, link_text="Contact Us", source_section="nav"),
            CandidateURL(url="https://example.com/refund-policy", page_type=PageType.POLICY, link_text="Refunds", source_section="footer"),
            CandidateURL(url="https://example.com/faq", page_type=PageType.FAQ, link_text="FAQ", source_section="nav"),
        ]

        result = select_pages_deterministic(
            candidates=candidates,
            max_pages=5,
            base_url="https://example.com",
            exclude_homepage=True,
        )

        selected_urls = [p.url for p in result.selected_pages]
        # None of the low-value policy pages should be in top 5
        self.assertNotIn("https://example.com/privacy-policy", selected_urls)
        self.assertNotIn("https://example.com/terms-and-conditions", selected_urls)
        self.assertNotIn("https://example.com/refund-policy", selected_urls)

        # High-value pages should be selected
        self.assertIn("https://example.com/about", selected_urls)
        self.assertIn("https://example.com/pricing", selected_urls)

        # Fallback candidates should exist and contain the policy pages
        fallback_urls = [p.url for p in result.fallback_candidates]
        self.assertTrue(len(fallback_urls) > 0)
        self.assertIn("https://example.com/privacy-policy", fallback_urls)

    def test_collection_page_capping(self) -> None:
        """Collection pages are capped at 2, prioritizing detail pages for remaining slots."""
        candidates = [
            CandidateURL(url="https://example.com/collections/eyes", page_type=PageType.PRODUCT, link_text="Eye Makeup", source_section="nav"),
            CandidateURL(url="https://example.com/collections/lips", page_type=PageType.PRODUCT, link_text="Lipstick", source_section="nav"),
            CandidateURL(url="https://example.com/collections/face", page_type=PageType.PRODUCT, link_text="Face", source_section="nav"),
            CandidateURL(url="https://example.com/collections/nails", page_type=PageType.PRODUCT, link_text="Nails", source_section="nav"),
            CandidateURL(url="https://example.com/products/matte-lipstick", page_type=PageType.PRODUCT, link_text="Matte Lipstick", source_section="content"),
            CandidateURL(url="https://example.com/products/liquid-eyeliner", page_type=PageType.PRODUCT, link_text="Liquid Eyeliner", source_section="content"),
        ]

        result = select_pages_deterministic(
            candidates=candidates,
            max_pages=4,
            base_url="https://example.com",
            exclude_homepage=True,
        )

        selected_urls = [p.url for p in result.selected_pages]
        collection_count = sum(1 for u in selected_urls if "/collections/" in u)
        self.assertLessEqual(collection_count, 2)
        # Detail products should be picked over 3rd and 4th collections
        self.assertTrue(any("/products/" in u for u in selected_urls))

    async def test_crawler_dynamic_fallback_replacement(self) -> None:
        """When initial crawling fails on some pages, fallback candidates are dynamically scraped."""
        primary_pages = [
            SelectedPage(url="https://example.com/page1", page_type=PageType.PRODUCT),
            SelectedPage(url="https://example.com/page2", page_type=PageType.PRODUCT),
        ]
        fallback_pages = [
            SelectedPage(url="https://example.com/page3", page_type=PageType.ABOUT),
            SelectedPage(url="https://example.com/page4", page_type=PageType.CONTACT),
        ]

        # First call fails page2 and succeeds page1; second call (fallback) succeeds page3
        first_crawl_result = WebsiteCrawlResult(
            website_url="https://example.com",
            status="partial",
            scraped_pages=[
                ScrapedPage(url="https://example.com/page1", content="Detailed content for page 1" * 20),
            ],
            failed_pages=[
                FailedPageScrape(url="https://example.com/page2", error_message="404 Not Found"),
            ],
            total_requested=2,
            total_scraped=1,
            total_failed=1,
            has_usable_content=True,
        )

        fallback_crawl_result = WebsiteCrawlResult(
            website_url="https://example.com",
            status="success",
            scraped_pages=[
                ScrapedPage(url="https://example.com/page3", content="Detailed content for page 3" * 20),
            ],
            failed_pages=[],
            total_requested=1,
            total_scraped=1,
            total_failed=0,
            has_usable_content=True,
        )

        state = {
            "selected_pages": primary_pages,
            "selection_result": PageSelectionResult(
                selected_pages=primary_pages,
                fallback_candidates=fallback_pages,
                total_selected=2,
                selection_method="deterministic_fallback",
            ),
            "business_form": {"business_website": "https://example.com"},
            "homepage_scraped_page": ScrapedPage(url="https://example.com", content="Homepage content" * 20),
        }

        with patch("backend.app.agent.nodes.website_crawling_node.crawl_selected_pages", side_effect=[first_crawl_result, fallback_crawl_result]) as mock_crawl:
            out_state = await website_crawling_node(state)
            self.assertEqual(out_state["workflow_status"], "running")
            # Should have called crawl_selected_pages twice (initial + fallback)
            self.assertEqual(mock_crawl.call_count, 2)
            # Scraped pages should contain both page1 and page3 (plus homepage merged)
            scraped_urls = [p.url for p in out_state["scraped_pages"]]
            self.assertIn("https://example.com/page1", scraped_urls)
            self.assertIn("https://example.com/page3", scraped_urls)


if __name__ == "__main__":
    unittest.main()

