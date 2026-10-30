"""Phase 1 — Homepage Reuse Tests.

Verifies that:
1. ``WebsiteURLDiscoveryResult`` carries the ``homepage_fetch_result`` from
   ``discover_website_urls()`` (sitemap_fallback_service).
2. ``_homepage_html_to_scraped_page()`` correctly converts HTML to a ``ScrapedPage``
   and rejects only genuinely invalid inputs (failed fetches, empty HTML, empty text).
   No minimum-length threshold is applied and no upper character cap is enforced.
3. The URL Discovery node stores ``homepage_scraped_page`` in the state update.
4. The Website Crawling node merges ``homepage_scraped_page`` into ``scraped_pages``.
5. Duplicate homepage URLs are not inserted twice.
6. Homepage fetch failure leads to ``homepage_scraped_page = None`` without breaking the workflow.

No real Apify or LLM calls are made; all external services are mocked.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import backend.app.main  # Ensure canonical import order
from backend.app.schemas.enums import PageType
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.homepage_service import HomepageFetchResult
from backend.app.services.sitemap_fallback_service import WebsiteURLDiscoveryResult
from backend.app.services.url_extractor_service import CandidateURL
from backend.app.services.website_content_crawler_service import WebsiteCrawlResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HOMEPAGE_URL = "https://www.example.com"
_HOMEPAGE_HTML = """
<html>
<head><title>Example Company</title></head>
<body>
<nav><a href="/products">Products</a> <a href="/about">About</a></nav>
<main>
<h1>Welcome to Example Company</h1>
<p>We make world-class widgets for every industry.
   Our widgets are trusted by over 10,000 businesses worldwide.
   We offer three product lines: Standard, Pro, and Enterprise.
   Contact us today to find out which tier is right for you.</p>
<p>Founded in 2010, Example Company has grown to serve customers across 50 countries.
   Our commitment to quality and innovation drives everything we do.</p>
</main>
<footer><p>© 2024 Example Company</p></footer>
</body>
</html>
"""


def _make_homepage_fetch_result(
    status: str = "success",
    html: str | None = None,
    final_url: str | None = None,
) -> HomepageFetchResult:
    """Build a HomepageFetchResult for testing."""
    return HomepageFetchResult(
        requested_url=_HOMEPAGE_URL,
        final_url=final_url or _HOMEPAGE_URL,
        html=html if html is not None else _HOMEPAGE_HTML,
        status=status,
        http_status_code=200 if status == "success" else None,
        error_message=None if status == "success" else "fetch failed",
        execution_time_seconds=0.5,
        actor_run_id=None,
    )


def _make_discovery_result(
    homepage_fetch_result: HomepageFetchResult | None = None,
) -> WebsiteURLDiscoveryResult:
    """Build a minimal WebsiteURLDiscoveryResult for testing."""
    candidates = [
        CandidateURL(
            url="https://www.example.com/products",
            link_text="Products",
            page_type=PageType.PRODUCT,
        )
    ]
    return WebsiteURLDiscoveryResult(
        requested_url=_HOMEPAGE_URL,
        base_url=_HOMEPAGE_URL,
        status="success",
        homepage_status="success",
        homepage_candidate_count=1,
        candidate_urls=candidates,
        total_candidates=1,
        homepage_fetch_result=homepage_fetch_result,
    )


def _make_crawl_result(pages: list[ScrapedPage]) -> WebsiteCrawlResult:
    """Build a minimal successful WebsiteCrawlResult."""
    return WebsiteCrawlResult(
        website_url=_HOMEPAGE_URL,
        status="success",
        scraped_pages=pages,
        failed_pages=[],
        total_requested=len(pages),
        total_scraped=len(pages),
        total_failed=0,
        has_usable_content=bool(pages),
    )


# ---------------------------------------------------------------------------
# Tests: _homepage_html_to_scraped_page conversion helper
# ---------------------------------------------------------------------------

class TestHomepageHtmlToScrapedPage(unittest.TestCase):
    """Unit tests for the _homepage_html_to_scraped_page conversion helper.

    Length restrictions removed: no minimum-length threshold, no 6,000-character cap.
    Only genuinely empty or failed content is rejected.
    """

    def setUp(self) -> None:
        # Import lazily so module-level fixtures resolve correctly
        from backend.app.agent.nodes.url_discovery_node import _homepage_html_to_scraped_page
        self._convert = _homepage_html_to_scraped_page

    def test_successful_conversion_returns_scraped_page(self) -> None:
        """Homepage HTML with sufficient content is converted to ScrapedPage."""
        result = self._convert(_make_homepage_fetch_result())
        self.assertIsNotNone(result)
        self.assertIsInstance(result, ScrapedPage)
        self.assertEqual(result.page_type, PageType.HOME)
        self.assertEqual(result.url, _HOMEPAGE_URL)
        self.assertGreater(len(result.content), 0)
        self.assertIn("Example Company", result.content)

    def test_failed_fetch_status_returns_none(self) -> None:
        """A non-success fetch status must yield None."""
        for bad_status in ("failed", "blocked", "empty_response"):
            with self.subTest(status=bad_status):
                result = self._convert(_make_homepage_fetch_result(status=bad_status))
                self.assertIsNone(result)

    def test_empty_html_returns_none(self) -> None:
        """Empty HTML string must yield None."""
        result = self._convert(_make_homepage_fetch_result(html=""))
        self.assertIsNone(result)

    def test_whitespace_only_html_returns_none(self) -> None:
        """Whitespace-only HTML must yield None."""
        result = self._convert(_make_homepage_fetch_result(html="   \n\t  "))
        self.assertIsNone(result)

    def test_sparse_html_preserved(self) -> None:
        """Short but non-empty homepage text is preserved — no minimum-length threshold."""
        sparse_html = "<html><body><p>Hi</p></body></html>"
        result = self._convert(_make_homepage_fetch_result(html=sparse_html))
        self.assertIsNotNone(result)
        self.assertIsInstance(result, ScrapedPage)
        self.assertIn("Hi", result.content)

    def test_page_type_is_home(self) -> None:
        """Converted page must have PageType.HOME."""
        result = self._convert(_make_homepage_fetch_result())
        self.assertIsNotNone(result)
        self.assertEqual(result.page_type, PageType.HOME)

    def test_uses_final_url_when_available(self) -> None:
        """When final_url differs from requested_url, the ScrapedPage uses final_url."""
        final = "https://www.example.com/"
        result = self._convert(_make_homepage_fetch_result(final_url=final))
        self.assertIsNotNone(result)
        self.assertEqual(result.url, final)

    def test_uses_requested_url_when_final_url_is_none(self) -> None:
        """Falls back to requested_url when final_url is absent."""
        hp = _make_homepage_fetch_result()
        hp = HomepageFetchResult(
            requested_url=_HOMEPAGE_URL,
            final_url=None,
            html=_HOMEPAGE_HTML,
            status="success",
            http_status_code=200,
            error_message=None,
            execution_time_seconds=0.5,
            actor_run_id=None,
        )
        result = self._convert(hp)
        self.assertIsNotNone(result)
        self.assertEqual(result.url, _HOMEPAGE_URL)

    def test_long_content_preserved_in_full(self) -> None:
        """Homepage content longer than 6,000 characters is preserved completely, not truncated."""
        long_text = "Widget information about our products and services. " * 500
        long_html = f"<html><body><p>{long_text}</p></body></html>"
        result = self._convert(_make_homepage_fetch_result(html=long_html))
        self.assertIsNotNone(result)
        # Extracted text must be longer than the old 6,000-char cap
        self.assertGreater(len(result.content), 6_000)

    def test_empty_extracted_text_returns_none(self) -> None:
        """HTML that yields only whitespace after extraction must return None."""
        # Tags-only HTML: BeautifulSoup extracts an empty string
        tags_only_html = "<html><head></head><body><div></div></body></html>"
        result = self._convert(_make_homepage_fetch_result(html=tags_only_html))
        self.assertIsNone(result)

    def test_status_code_preserved(self) -> None:
        """HTTP status code from the fetch result is propagated to ScrapedPage."""
        result = self._convert(_make_homepage_fetch_result())
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 200)


# ---------------------------------------------------------------------------
# Tests: WebsiteURLDiscoveryResult carries homepage_fetch_result
# ---------------------------------------------------------------------------

class TestWebsiteURLDiscoveryResultHomepageFetchResult(unittest.TestCase):
    """Verify that homepage_fetch_result field exists and is accessible."""

    def test_field_defaults_to_none(self) -> None:
        """homepage_fetch_result defaults to None when not provided."""
        result = _make_discovery_result(homepage_fetch_result=None)
        self.assertIsNone(result.homepage_fetch_result)

    def test_field_carries_fetch_result(self) -> None:
        """homepage_fetch_result is populated when provided."""
        hp = _make_homepage_fetch_result()
        result = _make_discovery_result(homepage_fetch_result=hp)
        self.assertIsNotNone(result.homepage_fetch_result)
        self.assertEqual(result.homepage_fetch_result.status, "success")

    def test_field_excluded_from_serialization(self) -> None:
        """homepage_fetch_result must not appear in the public JSON output."""
        hp = _make_homepage_fetch_result()
        result = _make_discovery_result(homepage_fetch_result=hp)
        serialized = result.model_dump()
        self.assertNotIn("homepage_fetch_result", serialized)

    def test_other_fields_unaffected(self) -> None:
        """Adding homepage_fetch_result does not disturb existing fields."""
        hp = _make_homepage_fetch_result()
        result = _make_discovery_result(homepage_fetch_result=hp)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.total_candidates, 1)
        self.assertEqual(result.homepage_status, "success")


# ---------------------------------------------------------------------------
# Tests: URL Discovery Node stores homepage_scraped_page
# ---------------------------------------------------------------------------

class TestURLDiscoveryNodeHomepagePage(unittest.IsolatedAsyncioTestCase):
    """Integration tests for the URL Discovery node's homepage-to-ScrapedPage logic."""

    def _make_state(self) -> dict:
        from backend.app.schemas.business_form import BusinessForm
        return {
            "business_form": BusinessForm(
                business_name_and_description="Example Co makes widgets",
                product_or_service="widgets",
                target_audience="businesses",
                audience_unknown=False,
                business_website=_HOMEPAGE_URL,
            )
        }

    async def test_node_stores_homepage_scraped_page_on_success(self) -> None:
        """URL Discovery node stores a ScrapedPage when homepage fetch succeeds."""
        from backend.app.agent.nodes.url_discovery_node import url_discovery_node

        hp = _make_homepage_fetch_result()
        discovery = _make_discovery_result(homepage_fetch_result=hp)

        with patch(
            "backend.app.agent.nodes.url_discovery_node.discover_website_urls",
            new=AsyncMock(return_value=discovery),
        ):
            result = await url_discovery_node(self._make_state())

        self.assertIn("homepage_scraped_page", result)
        page = result["homepage_scraped_page"]
        self.assertIsNotNone(page)
        self.assertIsInstance(page, ScrapedPage)
        self.assertEqual(page.page_type, PageType.HOME)

    async def test_node_stores_none_when_fetch_failed(self) -> None:
        """URL Discovery node sets homepage_scraped_page=None when homepage fetch failed."""
        from backend.app.agent.nodes.url_discovery_node import url_discovery_node

        hp = _make_homepage_fetch_result(status="failed", html=None)
        discovery = _make_discovery_result(homepage_fetch_result=hp)

        with patch(
            "backend.app.agent.nodes.url_discovery_node.discover_website_urls",
            new=AsyncMock(return_value=discovery),
        ):
            result = await url_discovery_node(self._make_state())

        self.assertIn("homepage_scraped_page", result)
        self.assertIsNone(result["homepage_scraped_page"])

    async def test_node_stores_none_when_no_fetch_result(self) -> None:
        """URL Discovery node sets homepage_scraped_page=None when discovery provides no fetch result."""
        from backend.app.agent.nodes.url_discovery_node import url_discovery_node

        discovery = _make_discovery_result(homepage_fetch_result=None)

        with patch(
            "backend.app.agent.nodes.url_discovery_node.discover_website_urls",
            new=AsyncMock(return_value=discovery),
        ):
            result = await url_discovery_node(self._make_state())

        self.assertIn("homepage_scraped_page", result)
        self.assertIsNone(result["homepage_scraped_page"])

    async def test_node_workflow_status_is_running_on_success(self) -> None:
        """URL Discovery node returns workflow_status='running' alongside homepage page."""
        from backend.app.agent.nodes.url_discovery_node import url_discovery_node

        hp = _make_homepage_fetch_result()
        discovery = _make_discovery_result(homepage_fetch_result=hp)

        with patch(
            "backend.app.agent.nodes.url_discovery_node.discover_website_urls",
            new=AsyncMock(return_value=discovery),
        ):
            result = await url_discovery_node(self._make_state())

        self.assertEqual(result["workflow_status"], "running")

    async def test_node_handles_conversion_exception_gracefully(self) -> None:
        """If _homepage_html_to_scraped_page raises, the node still succeeds."""
        from backend.app.agent.nodes.url_discovery_node import url_discovery_node

        hp = _make_homepage_fetch_result()
        discovery = _make_discovery_result(homepage_fetch_result=hp)

        with patch(
            "backend.app.agent.nodes.url_discovery_node.discover_website_urls",
            new=AsyncMock(return_value=discovery),
        ), patch(
            "backend.app.agent.nodes.url_discovery_node._homepage_html_to_scraped_page",
            side_effect=RuntimeError("boom"),
        ):
            result = await url_discovery_node(self._make_state())

        # Node must not fail; homepage_scraped_page should be None
        self.assertEqual(result["workflow_status"], "running")
        self.assertIsNone(result.get("homepage_scraped_page"))


# ---------------------------------------------------------------------------
# Tests: Website Crawling Node merges homepage_scraped_page
# ---------------------------------------------------------------------------

class TestWebsiteCrawlingNodeHomepageMerge(unittest.IsolatedAsyncioTestCase):
    """Integration tests for the Website Crawling node's homepage merge logic."""

    def _make_homepage_page(self, url: str = _HOMEPAGE_URL) -> ScrapedPage:
        return ScrapedPage(
            url=url,
            title="Home",
            content="Welcome to Example Company. We make world-class widgets.",
            page_type=PageType.HOME,
            status_code=200,
            metadata={},
        )

    def _make_product_page(self) -> ScrapedPage:
        return ScrapedPage(
            url="https://www.example.com/products",
            title="Products",
            content="Our product range includes Standard, Pro, and Enterprise widgets.",
            page_type=PageType.PRODUCT,
            status_code=200,
            metadata={},
        )

    def _make_state(
        self,
        homepage_scraped_page: ScrapedPage | None = None,
        extra: dict | None = None,
    ) -> dict:
        from backend.app.services.page_selection_service import SelectedPage
        state: dict = {
            "selected_pages": [
                SelectedPage(url="https://www.example.com/products", page_type=PageType.PRODUCT)
            ],
            "discovery_result": _make_discovery_result(),
            "homepage_scraped_page": homepage_scraped_page,
        }
        if extra:
            state.update(extra)
        return state

    async def test_homepage_prepended_to_scraped_pages(self) -> None:
        """Homepage ScrapedPage is prepended to the crawled pages list."""
        from backend.app.agent.nodes.website_crawling_node import website_crawling_node

        hp_page = self._make_homepage_page()
        product_page = self._make_product_page()
        crawl_result = _make_crawl_result([product_page])

        with patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=crawl_result),
        ):
            result = await website_crawling_node(self._make_state(homepage_scraped_page=hp_page))

        pages = result["scraped_pages"]
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0].url, _HOMEPAGE_URL)
        self.assertEqual(pages[0].page_type, PageType.HOME)
        self.assertEqual(pages[1].url, "https://www.example.com/products")

    async def test_homepage_not_duplicated_when_already_crawled(self) -> None:
        """Homepage is not inserted if the crawl already returned the same URL."""
        from backend.app.agent.nodes.website_crawling_node import website_crawling_node

        hp_page = self._make_homepage_page(url="https://www.example.com/")
        # Crawl returns the same page (different trailing slash)
        crawled_home = ScrapedPage(
            url="https://www.example.com",
            title="Home crawled",
            content="Welcome to Example Company, crawled version of the homepage content here.",
            page_type=PageType.HOME,
            status_code=200,
            metadata={},
        )
        crawl_result = _make_crawl_result([crawled_home])

        with patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=crawl_result),
        ):
            result = await website_crawling_node(self._make_state(homepage_scraped_page=hp_page))

        pages = result["scraped_pages"]
        # Only 1 page — the crawled one; homepage not duplicated
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].title, "Home crawled")

    async def test_no_homepage_page_leaves_scraped_pages_unchanged(self) -> None:
        """When homepage_scraped_page is None, scraped_pages stays as-is."""
        from backend.app.agent.nodes.website_crawling_node import website_crawling_node

        product_page = self._make_product_page()
        crawl_result = _make_crawl_result([product_page])

        with patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=crawl_result),
        ):
            result = await website_crawling_node(self._make_state(homepage_scraped_page=None))

        pages = result["scraped_pages"]
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].url, "https://www.example.com/products")

    async def test_homepage_scraped_page_preserved_in_state_update(self) -> None:
        """The homepage_scraped_page field is passed through in the crawling node's state update."""
        from backend.app.agent.nodes.website_crawling_node import website_crawling_node

        hp_page = self._make_homepage_page()
        product_page = self._make_product_page()
        crawl_result = _make_crawl_result([product_page])

        with patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=crawl_result),
        ):
            result = await website_crawling_node(self._make_state(homepage_scraped_page=hp_page))

        self.assertIn("homepage_scraped_page", result)
        self.assertIs(result["homepage_scraped_page"], hp_page)

    async def test_workflow_succeeds_without_homepage_page(self) -> None:
        """Workflow_status remains 'running' when homepage_scraped_page is absent."""
        from backend.app.agent.nodes.website_crawling_node import website_crawling_node

        product_page = self._make_product_page()
        crawl_result = _make_crawl_result([product_page])

        with patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=crawl_result),
        ):
            result = await website_crawling_node(self._make_state(homepage_scraped_page=None))

        self.assertEqual(result["workflow_status"], "running")

    async def test_homepage_page_is_first_in_list(self) -> None:
        """Homepage ScrapedPage is always at index 0 when merged."""
        from backend.app.agent.nodes.website_crawling_node import website_crawling_node

        hp_page = self._make_homepage_page()
        pages = [self._make_product_page()]
        crawl_result = _make_crawl_result(pages)

        with patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=crawl_result),
        ):
            result = await website_crawling_node(self._make_state(homepage_scraped_page=hp_page))

        self.assertEqual(result["scraped_pages"][0].page_type, PageType.HOME)

    async def test_homepage_preserved_when_crawling_raises_exception(self) -> None:
        """Scenario 2: Homepage is preserved in scraped_pages when internal crawling raises an exception."""
        from backend.app.agent.nodes.website_crawling_node import website_crawling_node

        hp_page = self._make_homepage_page()

        with patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            side_effect=RuntimeError("Apify connection timed out"),
        ):
            result = await website_crawling_node(self._make_state(homepage_scraped_page=hp_page))

        self.assertEqual(len(result["scraped_pages"]), 1)
        self.assertEqual(result["scraped_pages"][0].url, _HOMEPAGE_URL)
        self.assertEqual(result["workflow_status"], "running")
        self.assertEqual(result["crawl_result"].status, "failed")
        self.assertTrue(result["crawl_result"].has_usable_content)

    async def test_homepage_preserved_when_crawling_returns_empty_result(self) -> None:
        """Scenario 3: Homepage is preserved in scraped_pages when internal crawling returns empty result."""
        from backend.app.agent.nodes.website_crawling_node import website_crawling_node

        hp_page = self._make_homepage_page()
        empty_crawl = WebsiteCrawlResult(
            website_url=_HOMEPAGE_URL,
            status="empty",
            scraped_pages=[],
            failed_pages=[],
            total_requested=1,
            total_scraped=0,
            total_failed=1,
            has_usable_content=False,
            error_message="Zero usable pages returned.",
        )

        with patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=empty_crawl),
        ):
            result = await website_crawling_node(self._make_state(homepage_scraped_page=hp_page))

        self.assertEqual(len(result["scraped_pages"]), 1)
        self.assertEqual(result["scraped_pages"][0].url, _HOMEPAGE_URL)
        self.assertEqual(result["workflow_status"], "running")
        self.assertTrue(result["crawl_result"].has_usable_content)

    async def test_homepage_preserved_when_crawling_has_no_usable_content(self) -> None:
        """Scenario 4: Homepage is preserved when internal crawling failed and has_usable_content is False."""
        from backend.app.agent.nodes.website_crawling_node import website_crawling_node
        from backend.app.services.website_content_crawler_service import FailedPageScrape

        hp_page = self._make_homepage_page()
        failed_crawl = WebsiteCrawlResult(
            website_url=_HOMEPAGE_URL,
            status="failed",
            scraped_pages=[],
            failed_pages=[
                FailedPageScrape(
                    url="https://www.example.com/products",
                    error_message="WAF block",
                    page_type=PageType.PRODUCT,
                )
            ],
            total_requested=1,
            total_scraped=0,
            total_failed=1,
            has_usable_content=False,
            error_message="All requested pages failed.",
        )

        with patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=failed_crawl),
        ):
            result = await website_crawling_node(self._make_state(homepage_scraped_page=hp_page))

        self.assertEqual(len(result["scraped_pages"]), 1)
        self.assertEqual(result["scraped_pages"][0].url, _HOMEPAGE_URL)
        self.assertEqual(result["workflow_status"], "running")
        self.assertTrue(result["crawl_result"].has_usable_content)

    async def test_homepage_preserved_when_selected_pages_is_empty(self) -> None:
        """Homepage is preserved when selected_pages in state is empty."""
        from backend.app.agent.nodes.website_crawling_node import website_crawling_node

        hp_page = self._make_homepage_page()
        state = self._make_state(homepage_scraped_page=hp_page)
        state["selected_pages"] = []

        result = await website_crawling_node(state)

        self.assertEqual(len(result["scraped_pages"]), 1)
        self.assertEqual(result["scraped_pages"][0].url, _HOMEPAGE_URL)
        self.assertEqual(result["workflow_status"], "running")
        self.assertTrue(result["crawl_result"].has_usable_content)

    async def test_failure_behavior_preserved_when_no_homepage_and_crawling_fails(self) -> None:
        """Scenario 7: When homepage is absent and crawling fails, workflow fails with 0 scraped pages."""
        from backend.app.agent.nodes.website_crawling_node import website_crawling_node

        empty_crawl = WebsiteCrawlResult(
            website_url=_HOMEPAGE_URL,
            status="failed",
            scraped_pages=[],
            failed_pages=[],
            total_requested=1,
            total_scraped=0,
            total_failed=1,
            has_usable_content=False,
            error_message="Crawl failed",
        )

        with patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=empty_crawl),
        ):
            result = await website_crawling_node(self._make_state(homepage_scraped_page=None))

        self.assertEqual(len(result["scraped_pages"]), 0)
        self.assertEqual(result["workflow_status"], "failed")
        self.assertFalse(result["crawl_result"].has_usable_content)

    async def test_duplicate_removal_with_www_and_trailing_slash(self) -> None:
        """Scenario 6: Duplicate removal handles www, trailing slash, and casing variations."""
        from backend.app.agent.nodes.website_crawling_node import website_crawling_node

        hp_page = self._make_homepage_page(url="https://example.com")
        crawled_home = ScrapedPage(
            url="https://www.example.com/",
            title="Crawled Home",
            content="Welcome to Example Company duplicate text here.",
            page_type=PageType.HOME,
            status_code=200,
            metadata={},
        )
        crawl_result = _make_crawl_result([crawled_home])

        with patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=crawl_result),
        ):
            result = await website_crawling_node(self._make_state(homepage_scraped_page=hp_page))

        self.assertEqual(len(result["scraped_pages"]), 1)
        self.assertEqual(result["scraped_pages"][0].title, "Crawled Home")


# ---------------------------------------------------------------------------
# Tests: Business Overview with Homepage Only
# ---------------------------------------------------------------------------

class TestBusinessOverviewWithHomepage(unittest.IsolatedAsyncioTestCase):
    """Tests verifying Business Overview compatibility with homepage content."""

    def _make_business_form(self) -> Any:
        from backend.app.schemas.business_form import BusinessForm
        return BusinessForm(
            business_name_and_description="Example Co makes widgets",
            product_or_service="widgets",
            target_audience="businesses",
            audience_unknown=False,
            business_website=_HOMEPAGE_URL,
        )

    async def test_url_discovery_preserves_homepage_with_zero_candidate_urls(self) -> None:
        """Scenario 1: Homepage succeeds, but URL Discovery finds zero internal-page URLs."""
        from backend.app.agent.nodes.url_discovery_node import url_discovery_node

        hp = _make_homepage_fetch_result(status="success")
        discovery_zero_candidates = WebsiteURLDiscoveryResult(
            requested_url=_HOMEPAGE_URL,
            base_url=_HOMEPAGE_URL,
            status="failed",
            homepage_status="success",
            homepage_candidate_count=0,
            candidate_urls=[],
            total_candidates=0,
            homepage_fetch_result=hp,
        )

        with patch(
            "backend.app.agent.nodes.url_discovery_node.discover_website_urls",
            new=AsyncMock(return_value=discovery_zero_candidates),
        ):
            result = await url_discovery_node({
                "business_form": self._make_business_form(),
            })

        self.assertIn("homepage_scraped_page", result)
        self.assertIsNotNone(result["homepage_scraped_page"])
        self.assertEqual(result["homepage_scraped_page"].page_type, PageType.HOME)
        self.assertEqual(result["candidate_urls"], [])

    async def test_business_overview_succeeds_with_homepage_only(self) -> None:
        """Business Overview node uses homepage content when it is the only page available."""
        from backend.app.agent.nodes.business_overview_node import business_overview_node
        from backend.app.services.business_overview_service import (
            BusinessOverview,
            BusinessOverviewResult,
            TargetAudienceInfo,
        )

        hp_page = ScrapedPage(
            url=_HOMEPAGE_URL,
            title="Home",
            content="Welcome to Example Company. We manufacture world-class industrial widgets " * 10,
            page_type=PageType.HOME,
            status_code=200,
            metadata={},
        )

        mock_overview = BusinessOverview(
            business_name="Example Company",
            website_url=_HOMEPAGE_URL,
            business_description="Manufacturer of industrial widgets.",
            target_audience=TargetAudienceInfo(
                description="Businesses needing widgets",
                is_explicit=True,
            ),
        )
        mock_result = BusinessOverviewResult(
            status="success",
            business_overview=mock_overview,
            website_url=_HOMEPAGE_URL,
            total_pages_analyzed=1,
            total_findings=0,
            model_used="gpt-4o-mini",
        )

        with patch(
            "backend.app.agent.nodes.business_overview_node.generate_business_overview",
            new=AsyncMock(return_value=mock_result),
        ) as mock_gen:
            state = {
                "business_form": self._make_business_form(),
                "scraped_pages": [hp_page],
                "crawl_result": None,
            }
            res = await business_overview_node(state)

        self.assertEqual(res["workflow_status"], "completed")
        self.assertIsNotNone(res["business_overview"])
        # Ensure generate_business_overview was called with hp_page in crawl_input
        mock_gen.assert_called_once()
        call_input = mock_gen.call_args.kwargs.get("crawl_input")
        self.assertEqual(len(call_input), 1)
        self.assertEqual(call_input[0].url, _HOMEPAGE_URL)

    async def test_business_overview_fails_when_homepage_content_too_short(self) -> None:
        """Scenario 8: Homepage content too short (<150 chars) returns insufficient_content."""
        from backend.app.agent.nodes.business_overview_node import business_overview_node
        from backend.app.services.business_overview_service import generate_business_overview

        short_hp = ScrapedPage(
            url=_HOMEPAGE_URL,
            title="Short",
            content="Too short.",
            page_type=PageType.HOME,
            status_code=200,
            metadata={},
        )

        state = {
            "business_form": self._make_business_form(),
            "scraped_pages": [short_hp],
            "crawl_result": None,
        }
        res = await business_overview_node(state)

        self.assertEqual(res["workflow_status"], "failed")
        self.assertEqual(res["overview_result"].status, "insufficient_content")
        self.assertIsNone(res["business_overview"])

    async def test_business_overview_fails_when_no_usable_content_exists(self) -> None:
        """Scenario 9: No usable homepage or internal-page content yields failure."""
        from backend.app.agent.nodes.business_overview_node import business_overview_node

        state = {
            "business_form": self._make_business_form(),
            "scraped_pages": [],
            "crawl_result": None,
        }
        res = await business_overview_node(state)

        self.assertEqual(res["workflow_status"], "failed")
        self.assertEqual(res["overview_result"].status, "insufficient_content")
        self.assertIsNone(res["business_overview"])


if __name__ == "__main__":
    unittest.main()

