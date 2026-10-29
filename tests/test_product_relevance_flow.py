"""Targeted tests for Product-Relevant Page Selection and Business Overview Data Flow.

Verifies:
1. Candidate URL ranking prioritizes product-relevant URLs (e.g. lip glosses) over unrelated products.
2. Deterministic selection selects lip gloss collection and product pages when present.
3. Graceful fallback when no matching product pages are found.
4. BusinessForm data flow through LangGraph nodes into Page Selection and Business Overview.
5. Target audience and stated identity preservation in Business Overview synthesis.
6. Crawler input configurations (maxConcurrency, waitUntil=domcontentloaded).
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from backend.app.agent.nodes.business_overview_node import business_overview_node
from backend.app.agent.nodes.page_selection_node import page_selection_node
from backend.app.agent.nodes.url_discovery_node import url_discovery_node
from backend.app.agent.state import AgentState
from backend.app.schemas.business_form import BusinessForm
from backend.app.schemas.enums import PageType
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.business_overview_service import (
    BusinessOverview,
    BusinessOverviewResult,
    TargetAudienceInfo,
    _sanitize_and_validate_overview,
    generate_business_overview,
)
from backend.app.services.homepage_service import _build_actor_input as build_homepage_actor_input
from backend.app.services.page_selection_service import (
    calculate_product_relevance,
    select_pages_deterministic,
)
from backend.app.services.sitemap_fallback_service import (
    extract_product_keywords,
)
from backend.app.services.url_extractor_service import CandidateURL
from backend.app.services.website_content_crawler_service import (
    _build_actor_input as build_crawler_actor_input,
)


def _sample_swiss_beauty_candidates() -> list[CandidateURL]:
    """Sample candidate URLs mimicking Swiss Beauty eCommerce store."""
    return [
        CandidateURL(
            url="https://swissbeauty.in/",
            link_text="Home",
            page_type=PageType.HOME,
            source_section="nav",
        ),
        CandidateURL(
            url="https://swissbeauty.in/pages/about-us",
            link_text="About Us",
            page_type=PageType.ABOUT,
            source_section="nav",
        ),
        CandidateURL(
            url="https://swissbeauty.in/products/high-coverage-foundation",
            link_text="High Coverage Foundation",
            page_type=PageType.PRODUCT,
            source_section="sitemap",
        ),
        CandidateURL(
            url="https://swissbeauty.in/products/liquid-concealer",
            link_text="Liquid Concealer",
            page_type=PageType.PRODUCT,
            source_section="sitemap",
        ),
        CandidateURL(
            url="https://swissbeauty.in/products/oil-free-makeup-primer",
            link_text="Oil-Free Makeup Primer",
            page_type=PageType.PRODUCT,
            source_section="sitemap",
        ),
        CandidateURL(
            url="https://swissbeauty.in/products/ultimate-eyeshadow-palette",
            link_text="Ultimate Eyeshadow Palette",
            page_type=PageType.PRODUCT,
            source_section="sitemap",
        ),
        CandidateURL(
            url="https://swissbeauty.in/collections/lip-gloss",
            link_text="Lip Gloss Collection",
            page_type=PageType.PRODUCT,
            source_section="nav",
        ),
        CandidateURL(
            url="https://swissbeauty.in/products/plump-up-wet-lip-gloss",
            link_text="Plump Up Wet Lip Gloss",
            page_type=PageType.PRODUCT,
            source_section="sitemap",
        ),
        CandidateURL(
            url="https://swissbeauty.in/products/crystal-shine-lip-gloss",
            link_text="Crystal Shine Lip Gloss",
            page_type=PageType.PRODUCT,
            source_section="sitemap",
        ),
    ]


class TestProductRelevanceAndDataFlow(unittest.IsolatedAsyncioTestCase):
    """Test suite for product-relevance filtering, state propagation, and crawler configurations."""

    def test_extract_product_keywords(self):
        """Verify extraction of normalized keywords and singular stems from product description."""
        kws = extract_product_keywords("our lip glosses")
        self.assertIn("lip", kws)
        self.assertIn("gloss", kws)
        self.assertNotIn("our", kws)

    def test_calculate_product_relevance_scores(self):
        """Verify that relevant URLs score much higher than unrelated products."""
        keywords = ["lip", "gloss"]
        phrase = "our lip glosses"

        cand_lip_gloss = CandidateURL(
            url="https://swissbeauty.in/collections/lip-gloss",
            link_text="Lip Gloss Collection",
            page_type=PageType.PRODUCT,
        )
        cand_foundation = CandidateURL(
            url="https://swissbeauty.in/products/high-coverage-foundation",
            link_text="High Coverage Foundation",
            page_type=PageType.PRODUCT,
        )

        score_relevant = calculate_product_relevance(cand_lip_gloss, keywords, phrase)
        score_unrelated = calculate_product_relevance(cand_foundation, keywords, phrase)

        self.assertGreater(score_relevant, 150)
        self.assertEqual(score_unrelated, 0)

    def test_deterministic_page_selection_prioritizes_lip_glosses(self):
        """Verify that page selection prioritizes lip gloss pages and retains core context."""
        candidates = _sample_swiss_beauty_candidates()
        result = select_pages_deterministic(
            candidates,
            max_pages=4,
            base_url="https://swissbeauty.in",
            product_or_service="our lip glosses",
        )

        selected_urls = [p.url for p in result.selected_pages]
        # Homepage must be present for brand baseline
        self.assertIn("https://swissbeauty.in/", selected_urls)
        # Lip gloss pages must be selected
        self.assertIn("https://swissbeauty.in/collections/lip-gloss", selected_urls)
        self.assertIn("https://swissbeauty.in/products/plump-up-wet-lip-gloss", selected_urls)
        # Unrelated products must NOT displace lip glosses
        self.assertNotIn("https://swissbeauty.in/products/high-coverage-foundation", selected_urls)

    def test_deterministic_page_selection_graceful_fallback(self):
        """Verify graceful fallback when no matching product pages exist."""
        candidates = _sample_swiss_beauty_candidates()
        result = select_pages_deterministic(
            candidates,
            max_pages=5,
            base_url="https://swissbeauty.in",
            product_or_service="industrial hydraulic machinery",
        )

        self.assertEqual(result.total_selected, 5)
        selected_urls = [p.url for p in result.selected_pages]
        # Core pages are safely chosen
        self.assertIn("https://swissbeauty.in/", selected_urls)
        self.assertIn("https://swissbeauty.in/pages/about-us", selected_urls)
        # Verify selection reasons explain fallback
        reasons = [p.selection_reason for p in result.selected_pages]
        self.assertTrue(any("no matching pages for 'industrial hydraulic machinery' found" in r for r in reasons))

    async def test_page_selection_node_passes_product_or_service(self):
        """Verify that page_selection_node extracts product_or_service and passes it to the service."""
        form = BusinessForm(
            business_name_and_description="Swiss Beauty is a cosmetics brand offering quality beauty products.",
            product_or_service="our lip glosses",
            target_audience="Young women in India aged 18-35",
            audience_unknown=False,
            business_website="https://swissbeauty.in",
        )
        state: AgentState = {
            "business_form": form,
            "candidate_urls": _sample_swiss_beauty_candidates(),
        }

        with patch("backend.app.agent.nodes.page_selection_node.select_pages_with_llm") as mock_select:
            mock_select.return_value = select_pages_deterministic(
                candidates=_sample_swiss_beauty_candidates(),
                max_pages=4,
                base_url="https://swissbeauty.in",
                product_or_service="our lip glosses",
            )
            result = await page_selection_node(state)

            mock_select.assert_awaited_once()
            _, kwargs = mock_select.call_args
            self.assertEqual(kwargs.get("product_or_service"), "our lip glosses")
            self.assertEqual(result["workflow_status"], "running")

    async def test_business_overview_node_passes_business_form(self):
        """Verify that business_overview_node forwards business_form to generate_business_overview."""
        form = BusinessForm(
            business_name_and_description="Swiss Beauty is a cosmetics brand offering quality beauty products.",
            product_or_service="our lip glosses",
            target_audience="Young women in India aged 18-35",
            audience_unknown=False,
            business_website="https://swissbeauty.in",
        )
        scraped = [
            ScrapedPage(
                url="https://swissbeauty.in/collections/lip-gloss",
                title="Lip Gloss Collection",
                content="Swiss Beauty offers high-shine, non-sticky lip glosses enriched with jojoba oil.",
                page_type=PageType.PRODUCT,
            )
        ]
        state: AgentState = {
            "business_form": form,
            "scraped_pages": scraped,
        }

        with patch("backend.app.agent.nodes.business_overview_node.generate_business_overview") as mock_overview:
            mock_overview.return_value = BusinessOverviewResult(
                status="success",
                business_overview=BusinessOverview(
                    business_name="Swiss Beauty",
                    website_url="https://swissbeauty.in",
                    business_description="Affordable beauty brand",
                    target_audience=TargetAudienceInfo(
                        description="Young women in India aged 18-35",
                        is_explicit=True,
                    ),
                ),
                website_url="https://swissbeauty.in",
                total_pages_analyzed=1,
                total_findings=0,
            )
            result = await business_overview_node(state)

            mock_overview.assert_awaited_once()
            _, kwargs = mock_overview.call_args
            self.assertEqual(kwargs.get("business_form"), form)
            self.assertEqual(result["workflow_status"], "completed")

    def test_target_audience_and_identity_preservation(self):
        """Verify that _sanitize_and_validate_overview preserves user-supplied audience and identity."""
        form = BusinessForm(
            business_name_and_description="Swiss Beauty is an Indian beauty and cosmetics brand offering affordable makeup.",
            product_or_service="our lip glosses",
            target_audience="Women and young adults aged 18–35 in India seeking affordable makeup.",
            audience_unknown=False,
            business_website="https://swissbeauty.in",
        )
        raw_overview = BusinessOverview(
            business_name=None,
            website_url="https://swissbeauty.in",
            business_description="",
            target_audience=TargetAudienceInfo(
                description="Not specified on website",
                is_explicit=False,
            ),
        )

        sanitized = _sanitize_and_validate_overview(
            raw_overview,
            valid_source_urls={"https://swissbeauty.in"},
            fallback_website_url="https://swissbeauty.in",
            business_form=form,
        )

        # Target audience must be preserved from user form and marked explicit
        self.assertEqual(sanitized.target_audience.description, form.target_audience)
        self.assertTrue(sanitized.target_audience.is_explicit)
        # Business description must be preserved from user form
        self.assertEqual(sanitized.business_description, form.business_name_and_description)

    def test_crawler_concurrency_configured(self):
        """Verify that Website Content Crawler input configures maxConcurrency."""
        urls = ["https://swissbeauty.in/p1", "https://swissbeauty.in/p2", "https://swissbeauty.in/p3"]
        actor_input = build_crawler_actor_input(urls, timeout_seconds=120)

        self.assertEqual(actor_input.get("maxCrawlDepth"), 0)
        self.assertEqual(actor_input.get("maxConcurrency"), 3)
        self.assertIn("crawlerType", actor_input)

    def test_homepage_scraper_uses_domcontentloaded(self):
        """Verify that homepage Playwright scraper uses domcontentloaded instead of networkidle."""
        actor_input = build_homepage_actor_input("https://swissbeauty.in")
        self.assertEqual(actor_input.get("waitUntil"), "domcontentloaded")
        self.assertLessEqual(actor_input.get("pageLoadTimeoutSecs"), 30)


if __name__ == "__main__":
    unittest.main()
