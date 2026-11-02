"""Unit tests verifying the 6-page total scraping limit enforcement.

Requirements:
1. Homepage counts as 1 page (reused if available and valid).
2. Related pages: maximum 5 pages selected from discovered URLs, excluding homepage.
3. Total page limit: 6 (1 homepage + up to 5 related pages = 6 pages total).
4. Crawler receives no more than 5 additional URLs when homepage is already available.
5. Final collected content contains no more than 6 unique website pages, including homepage.
6. Duplicate URLs and duplicate homepage content are strictly prevented.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from backend.app.agent.nodes.page_selection_node import page_selection_node
from backend.app.agent.nodes.website_crawling_node import website_crawling_node
from backend.app.core.config import settings
from backend.app.schemas.business_form import BusinessForm
from backend.app.schemas.enums import PageType
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.page_selection_service import (
    PageSelectionResult,
    SelectedPage,
    select_pages_deterministic,
)
from backend.app.services.sitemap_fallback_service import WebsiteURLDiscoveryResult
from backend.app.services.url_extractor_service import CandidateURL
from backend.app.services.website_content_crawler_service import WebsiteCrawlResult


class TestPageLimitEnforcement(unittest.IsolatedAsyncioTestCase):
    """Test suite validating 6-page total limit and 5 related-pages limit."""

    def setUp(self):
        self.website_url = "https://www.example.com"
        self.business_form = BusinessForm(
            business_name_and_description="Example Corp manufactures premium industrial widgets and tools for global clients.",
            product_or_service="widgets",
            target_audience="all",
            audience_unknown=False,
            business_website=self.website_url,
        )
        self.homepage_page = ScrapedPage(
            url=self.website_url,
            title="Example Corp Home",
            content="Welcome to Example Corp. " * 30,  # > 150 chars, valid
            page_type=PageType.HOME,
            status_code=200,
            metadata={},
        )

    def _make_candidate(self, path: str, page_type: PageType) -> CandidateURL:
        return CandidateURL(
            url=f"{self.website_url}{path}",
            link_text=path.strip("/"),
            page_type=page_type,
            source_section="nav",
        )

    async def test_page_selection_selects_max_5_related_and_excludes_homepage(self):
        """When homepage is available, page selection selects at most 5 related pages and excludes homepage."""
        candidates = [
            self._make_candidate("", PageType.HOME),
            self._make_candidate("/about", PageType.ABOUT),
            self._make_candidate("/pricing", PageType.PRICING),
            self._make_candidate("/products", PageType.PRODUCT),
            self._make_candidate("/products/widget-a", PageType.PRODUCT),
            self._make_candidate("/products/widget-b", PageType.PRODUCT),
            self._make_candidate("/products/widget-c", PageType.PRODUCT),
            self._make_candidate("/contact", PageType.CONTACT),
            self._make_candidate("/faq", PageType.FAQ),
        ]

        state = {
            "business_form": self.business_form,
            "candidate_urls": candidates,
            "homepage_scraped_page": self.homepage_page,
            "discovery_result": WebsiteURLDiscoveryResult(
                requested_url=self.website_url,
                base_url=self.website_url,
                status="success",
                homepage_status="success",
                homepage_candidate_count=len(candidates),
                candidate_urls=candidates,
                total_candidates=len(candidates),
            ),
        }

        result = await page_selection_node(state)

        self.assertEqual(result["workflow_status"], "running")
        selected = result["selected_pages"]
        # Must not exceed 5 related pages
        self.assertLessEqual(len(selected), 5)
        self.assertEqual(len(selected), 5)
        # Exclude homepage URL
        selected_urls = [p.url.rstrip("/") for p in selected]
        self.assertNotIn(self.website_url.rstrip("/"), selected_urls)

    async def test_crawler_receives_max_5_urls_and_total_pages_capped_at_6(self):
        """Crawler receives at most 5 additional URLs and total merged pages never exceeds 6."""
        # 7 selected pages provided in state
        selected = [
            SelectedPage(url=f"{self.website_url}/p{i}", page_type=PageType.PRODUCT)
            for i in range(1, 8)
        ]

        crawl_result = WebsiteCrawlResult(
            website_url=self.website_url,
            status="success",
            scraped_pages=[
                ScrapedPage(
                    url=f"{self.website_url}/p{i}",
                    title=f"Page {i}",
                    content=f"Content for page {i} " * 20,
                    page_type=PageType.PRODUCT,
                    status_code=200,
                    metadata={},
                )
                for i in range(1, 6)
            ],
            failed_pages=[],
            total_requested=5,
            total_scraped=5,
            total_failed=0,
            has_usable_content=True,
        )

        with patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=crawl_result),
        ) as mock_crawl:
            state = {
                "business_form": self.business_form,
                "selected_pages": selected,
                "homepage_scraped_page": self.homepage_page,
            }
            res = await website_crawling_node(state)

            mock_crawl.assert_called_once()
            called_pages = mock_crawl.call_args.kwargs.get("pages")
            called_max_pages = mock_crawl.call_args.kwargs.get("max_pages")
            # Crawler must receive at most 5 URLs
            self.assertLessEqual(len(called_pages), 5)
            self.assertEqual(called_max_pages, 5)

            # Final collected content: 1 homepage + 5 crawled = 6 total
            final_pages = res["scraped_pages"]
            self.assertEqual(len(final_pages), 6)
            self.assertEqual(final_pages[0].url, self.website_url)
            self.assertEqual(res["crawl_result"].total_scraped, 6)

    async def test_fewer_than_5_related_pages_available(self):
        """When fewer than 5 related pages exist, scrape only available and do not exceed total."""
        candidates = [
            self._make_candidate("", PageType.HOME),
            self._make_candidate("/about", PageType.ABOUT),
            self._make_candidate("/pricing", PageType.PRICING),
        ]

        state = {
            "business_form": self.business_form,
            "candidate_urls": candidates,
            "homepage_scraped_page": self.homepage_page,
            "discovery_result": WebsiteURLDiscoveryResult(
                requested_url=self.website_url,
                base_url=self.website_url,
                status="success",
                homepage_status="success",
                homepage_candidate_count=3,
                candidate_urls=candidates,
                total_candidates=3,
            ),
        }

        result = await page_selection_node(state)
        selected = result["selected_pages"]
        # Only 2 related pages exist
        self.assertEqual(len(selected), 2)
        urls = [p.url for p in selected]
        self.assertIn(f"{self.website_url}/about", urls)
        self.assertIn(f"{self.website_url}/pricing", urls)
        self.assertNotIn(self.website_url, urls)

    def test_settings_configured_to_6(self):
        """Ensure settings.MAX_PAGES_USER_SITE is configured to 6."""
        self.assertEqual(settings.MAX_PAGES_USER_SITE, 6)


if __name__ == "__main__":
    unittest.main()
