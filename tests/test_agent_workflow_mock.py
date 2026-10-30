"""Mock Integration Test for Competitor Marketing Agent LangGraph Workflow.

Verifies end-to-end execution of the compiled LangGraph workflow:
    START -> url_discovery -> page_selection -> website_crawling -> business_overview -> END

Validates state propagation, node ordering, input/output contracts, and failure handling
using realistic synthetic mock data without invoking external APIs (OpenAI, Apify, etc.).
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from backend.app.agent.graph import create_graph, get_graph, graph
from backend.app.agent.state import AgentState
from backend.app.schemas.business_form import BusinessForm
from backend.app.schemas.enums import FindingType, PageType
from backend.app.schemas.scraped_data import Finding, ScrapedPage
from backend.app.services.business_overview_service import (
    BusinessOverview,
    BusinessOverviewResult,
    InformationGap,
    ProductServiceItem,
    TargetAudienceInfo,
    ValueProposition,
)
from backend.app.services.page_selection_service import (
    PageSelectionResult,
    SelectedPage,
)
from backend.app.services.sitemap_fallback_service import WebsiteURLDiscoveryResult
from backend.app.services.url_extractor_service import CandidateURL
from backend.app.services.website_content_crawler_service import (
    FailedPageScrape,
    WebsiteCrawlResult,
)


def _create_sample_business_form() -> BusinessForm:
    """Create a realistic, valid BusinessForm for a synthetic skincare business."""
    return BusinessForm(
        business_name_and_description=(
            "Lumina Botanicals is an organic clean-label skincare brand formulating "
            "plant-powered serums, barrier creams, and restorative treatments."
        ),
        product_or_service="Clean botanical serums, nourishing moisturizers, and gentle cleansers",
        target_audience="Eco-conscious consumers aged 25-50 seeking non-toxic, peptide-rich anti-aging skincare",
        audience_unknown=False,
        business_website="https://www.luminabotanicals.com",
    )


def _create_sample_discovery_result() -> tuple[WebsiteURLDiscoveryResult, list[CandidateURL]]:
    """Create realistic mock URL discovery output for Lumina Botanicals."""
    candidates = [
        CandidateURL(
            url="https://www.luminabotanicals.com",
            link_text="Home",
            page_type=PageType.HOME,
            source_section="nav",
        ),
        CandidateURL(
            url="https://www.luminabotanicals.com/products",
            link_text="Shop All Serums & Creams",
            page_type=PageType.PRODUCT,
            source_section="nav",
        ),
        CandidateURL(
            url="https://www.luminabotanicals.com/pricing",
            link_text="Product Pricing & Bundles",
            page_type=PageType.PRICING,
            source_section="nav",
        ),
        CandidateURL(
            url="https://www.luminabotanicals.com/about",
            link_text="Our Clean Beauty Philosophy",
            page_type=PageType.ABOUT,
            source_section="footer",
        ),
        CandidateURL(
            url="https://www.luminabotanicals.com/faq",
            link_text="Frequently Asked Questions",
            page_type=PageType.FAQ,
            source_section="footer",
        ),
    ]
    discovery_result = WebsiteURLDiscoveryResult(
        requested_url="https://www.luminabotanicals.com",
        base_url="https://www.luminabotanicals.com",
        status="success",
        homepage_status="success",
        homepage_candidate_count=4,
        sitemap_triggered=True,
        sitemap_trigger_reason="missing_important_categories: pricing",
        sitemap_status="success",
        sitemap_candidate_count=1,
        candidate_urls=candidates,
        total_candidates=len(candidates),
        covered_categories=["about", "pricing", "product_or_service"],
        missing_categories=["offer"],
    )
    return discovery_result, candidates


def _create_sample_selection_result() -> tuple[PageSelectionResult, list[SelectedPage]]:
    """Create realistic mock page selection output for Lumina Botanicals."""
    selected_pages = [
        SelectedPage(
            url="https://www.luminabotanicals.com/products",
            page_type=PageType.PRODUCT,
            link_text="Shop All Serums & Creams",
            source_section="nav",
            selection_reason="Showcases core botanical product catalog and formulations.",
        ),
        SelectedPage(
            url="https://www.luminabotanicals.com/pricing",
            page_type=PageType.PRICING,
            link_text="Product Pricing & Bundles",
            source_section="nav",
            selection_reason="Details pricing tiers, bottle sizes, and subscription discounts.",
        ),
        SelectedPage(
            url="https://www.luminabotanicals.com/about",
            page_type=PageType.ABOUT,
            link_text="Our Clean Beauty Philosophy",
            source_section="footer",
            selection_reason="Describes brand mission, founder background, and clinical testing.",
        ),
    ]
    selection_result = PageSelectionResult(
        selected_pages=selected_pages,
        total_selected=len(selected_pages),
        selection_method="llm",
        model_used="gpt-6-luna",
        error_message=None,
    )
    return selection_result, selected_pages


def _create_sample_crawl_result() -> tuple[WebsiteCrawlResult, list[ScrapedPage], list[FailedPageScrape]]:
    """Create realistic mock crawling output for Lumina Botanicals."""
    scraped_pages = [
        ScrapedPage(
            url="https://www.luminabotanicals.com/products",
            title="Clean Botanical Serums & Treatments | Lumina Botanicals",
            content=(
                "# Botanical Product Line\n\n"
                "### Lumina Active Peptide Radiance Serum\n"
                "Our flagship brightening serum combines multi-weight hyaluronic acid, vegan squalane, "
                "and copper peptides to improve skin firmness and hydration in 14 days.\n\n"
                "### Hydra-Barrier Botanical Cream\n"
                "Ceramide-infused rich moisturizer formulated with cold-pressed rosehip seed oil "
                "and chamomile extract to restore weakened lipid barriers."
            ),
            page_type=PageType.PRODUCT,
            status_code=200,
            metadata={"char_count": 480, "is_truncated": False},
        ),
        ScrapedPage(
            url="https://www.luminabotanicals.com/pricing",
            title="Pricing, Bundles & Subscriptions | Lumina Botanicals",
            content=(
                "# Skincare Pricing & Subscriptions\n\n"
                "- Lumina Active Peptide Serum: $58.00 (30ml bottle)\n"
                "- Hydra-Barrier Botanical Cream: $48.00 (50ml jar)\n"
                "- Complete Glow Trio Bundle: $120.00 (Save 20%)\n\n"
                "Subscribe & Save: Receive 15% off regular prices with free shipping on every recurring delivery."
            ),
            page_type=PageType.PRICING,
            status_code=200,
            metadata={"char_count": 360, "is_truncated": False},
        ),
        ScrapedPage(
            url="https://www.luminabotanicals.com/about",
            title="About Us - Clean Science & Botanical Purity | Lumina Botanicals",
            content=(
                "# About Lumina Botanicals\n\n"
                "Founded in 2021 by holistic dermatologists, Lumina Botanicals bridges the gap "
                "between clinical cosmetic chemistry and pure plant therapeutics. Every formula "
                "is cruelty-free, Leaping Bunny certified, and formulated without synthetic fragrance, "
                "sulfates, or parabens."
            ),
            page_type=PageType.ABOUT,
            status_code=200,
            metadata={"char_count": 390, "is_truncated": False},
        ),
    ]
    crawl_result = WebsiteCrawlResult(
        website_url="https://www.luminabotanicals.com",
        status="success",
        scraped_pages=scraped_pages,
        failed_pages=[],
        total_requested=len(scraped_pages),
        total_scraped=len(scraped_pages),
        total_failed=0,
        has_usable_content=True,
        execution_time_seconds=1.45,
    )
    return crawl_result, scraped_pages, []


def _create_sample_overview_result() -> tuple[BusinessOverviewResult, BusinessOverview]:
    """Create realistic mock business overview output for Lumina Botanicals."""
    overview = BusinessOverview(
        business_name="Lumina Botanicals",
        website_url="https://www.luminabotanicals.com",
        business_description=(
            "Lumina Botanicals is a clean, organic skincare brand founded in 2021 by holistic dermatologists, "
            "specializing in peptide-powered botanical serums and restorative ceramide barrier creams."
        ),
        products_and_services=[
            ProductServiceItem(
                name="Lumina Active Peptide Radiance Serum",
                description="Brightening and firming facial serum formulated with copper peptides and squalane ($58.00).",
                features=["Multi-weight hyaluronic acid", "Vegan squalane", "Copper peptides"],
                benefits=["Improves firmness and hydration in 14 days"],
                source_url="https://www.luminabotanicals.com/products",
            ),
            ProductServiceItem(
                name="Hydra-Barrier Botanical Cream",
                description="Ceramide-infused restorative moisturizer for weakened skin barriers ($48.00).",
                features=["Cold-pressed rosehip seed oil", "Chamomile extract", "Lipid ceramides"],
                benefits=["Restores weakened moisture barrier"],
                source_url="https://www.luminabotanicals.com/products",
            ),
        ],
        target_audience=TargetAudienceInfo(
            description="Eco-conscious consumers and skincare enthusiasts seeking clean, clinical-grade botanical formulations.",
            is_explicit=False,
            source_url="https://www.luminabotanicals.com/about",
        ),
        positioning_and_value_propositions=[
            ValueProposition(
                statement="Bridges clinical cosmetic chemistry with pure, cruelty-free botanical therapeutics.",
                is_explicit=True,
                source_url="https://www.luminabotanicals.com/about",
            ),
            ValueProposition(
                statement="Subscribe & Save model offering 15% recurring savings and free shipping.",
                is_explicit=True,
                source_url="https://www.luminabotanicals.com/pricing",
            ),
        ],
        information_gaps=[
            InformationGap(
                category="Retail Distribution",
                observation="Website does not specify physical retail stockists or in-store distribution partners.",
            ),
        ],
        key_findings=[
            Finding(
                statement="Active Peptide Serum priced at $58.00; Hydra-Barrier Cream priced at $48.00.",
                finding_type=FindingType.PRICING,
                source_url="https://www.luminabotanicals.com/pricing",
                is_inferred=False,
            ),
            Finding(
                statement="All products are Leaping Bunny certified cruelty-free and free of synthetic fragrance.",
                finding_type=FindingType.POSITIONING,
                source_url="https://www.luminabotanicals.com/about",
                is_inferred=False,
            ),
        ],
        sources_used=[
            "https://www.luminabotanicals.com/about",
            "https://www.luminabotanicals.com/pricing",
            "https://www.luminabotanicals.com/products",
        ],
    )
    overview_result = BusinessOverviewResult(
        status="success",
        business_overview=overview,
        website_url="https://www.luminabotanicals.com",
        total_pages_analyzed=3,
        total_findings=2,
        model_used="gpt-6-luna",
        execution_time_seconds=2.1,
    )
    return overview_result, overview


class TestAgentWorkflowMock(unittest.IsolatedAsyncioTestCase):
    """End-to-end unit test suite for the compiled LangGraph workflow with full service mocks."""

    async def test_full_successful_workflow_execution(self):
        """Verify complete sequential workflow execution from intake to business overview."""
        # 1. Setup realistic synthetic data
        business_form = _create_sample_business_form()
        discovery_result, candidate_urls = _create_sample_discovery_result()
        selection_result, selected_pages = _create_sample_selection_result()
        crawl_result, scraped_pages, failed_pages = _create_sample_crawl_result()
        overview_result, business_overview = _create_sample_overview_result()

        # 2. Track execution order across mock calls
        execution_order: list[str] = []

        async def mock_discover(website_url: str, **kwargs):
            execution_order.append("url_discovery")
            return discovery_result

        async def mock_select(candidates: list[CandidateURL], website_url: str, **kwargs):
            execution_order.append("page_selection")
            return selection_result

        async def mock_crawl(pages, website_url: str | None = None, **kwargs):
            execution_order.append("website_crawling")
            return crawl_result

        async def mock_overview(crawl_input, website_url: str | None = None, **kwargs):
            execution_order.append("business_overview")
            return overview_result

        # 3. Patch external services at node reference locations
        with patch(
            "backend.app.agent.nodes.url_discovery_node.discover_website_urls",
            side_effect=mock_discover,
        ) as patch_discover, patch(
            "backend.app.agent.nodes.page_selection_node.select_pages_with_llm",
            side_effect=mock_select,
        ) as patch_select, patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            side_effect=mock_crawl,
        ) as patch_crawl, patch(
            "backend.app.agent.nodes.business_overview_node.generate_business_overview",
            side_effect=mock_overview,
        ) as patch_overview:

            # 4. Invoke the compiled LangGraph workflow
            workflow_graph = get_graph()
            initial_state: AgentState = {
                "business_form": business_form,
                "workflow_status": "pending",
            }
            final_state = await workflow_graph.ainvoke(initial_state)

            # --- Assert Execution Order ---
            expected_order = [
                "url_discovery",
                "page_selection",
                "website_crawling",
                "business_overview",
            ]
            self.assertEqual(
                execution_order,
                expected_order,
                f"Execution order mismatch: {execution_order} != {expected_order}",
            )

            # --- Assert Mock Call Arguments ---
            patch_discover.assert_awaited_once_with(
                "https://www.luminabotanicals.com",
                product_or_service=business_form.product_or_service,
            )
            patch_select.assert_awaited_once_with(
                candidates=candidate_urls,
                website_url="https://www.luminabotanicals.com",
                product_or_service=business_form.product_or_service,
            )
            patch_crawl.assert_awaited_once_with(
                pages=selected_pages,
                website_url="https://www.luminabotanicals.com",
            )
            patch_overview.assert_awaited_once_with(
                crawl_input=crawl_result,
                website_url="https://www.luminabotanicals.com",
                business_form=business_form,
            )

            # --- Assert State Preservation & Propagation ---
            # User intake preserved as single source of truth
            self.assertEqual(final_state.get("business_form"), business_form)
            self.assertEqual(
                final_state["business_form"].business_name_and_description,
                business_form.business_name_and_description,
            )

            # URL discovery results preserved
            self.assertEqual(final_state.get("discovery_result"), discovery_result)
            self.assertEqual(final_state.get("candidate_urls"), candidate_urls)
            self.assertEqual(len(final_state["candidate_urls"]), 5)

            # Page selection results preserved
            self.assertEqual(final_state.get("selection_result"), selection_result)
            self.assertEqual(final_state.get("selected_pages"), selected_pages)
            self.assertEqual(len(final_state["selected_pages"]), 3)

            # Website crawl results preserved
            self.assertEqual(final_state.get("crawl_result"), crawl_result)
            self.assertEqual(final_state.get("scraped_pages"), scraped_pages)
            self.assertEqual(final_state.get("failed_pages"), failed_pages)
            self.assertEqual(len(final_state["scraped_pages"]), 3)

            # Business overview populated
            self.assertEqual(final_state.get("overview_result"), overview_result)
            self.assertEqual(final_state.get("business_overview"), business_overview)
            self.assertEqual(
                final_state["business_overview"].business_name,
                "Lumina Botanicals",
            )
            self.assertEqual(
                len(final_state["business_overview"].products_and_services), 2
            )

            # Terminal workflow status
            self.assertEqual(final_state.get("current_step"), "business_overview")
            self.assertEqual(final_state.get("workflow_status"), "completed")

    async def test_partial_crawl_success_workflow(self):
        """Verify workflow completes when crawling partially succeeds with usable content."""
        business_form = _create_sample_business_form()
        discovery_result, _ = _create_sample_discovery_result()
        selection_result, _ = _create_sample_selection_result()
        _, scraped_pages, _ = _create_sample_crawl_result()
        overview_result, business_overview = _create_sample_overview_result()

        # Partial crawl: 2 scraped, 1 failed
        partial_crawl = WebsiteCrawlResult(
            website_url="https://www.luminabotanicals.com",
            status="partial",
            scraped_pages=scraped_pages[:2],
            failed_pages=[
                FailedPageScrape(
                    url="https://www.luminabotanicals.com/about",
                    error_message="HTTP 503 Service Unavailable",
                    page_type=PageType.ABOUT,
                )
            ],
            total_requested=3,
            total_scraped=2,
            total_failed=1,
            has_usable_content=True,
        )

        with patch(
            "backend.app.agent.nodes.url_discovery_node.discover_website_urls",
            new=AsyncMock(return_value=discovery_result),
        ), patch(
            "backend.app.agent.nodes.page_selection_node.select_pages_with_llm",
            new=AsyncMock(return_value=selection_result),
        ), patch(
            "backend.app.agent.nodes.website_crawling_node.crawl_selected_pages",
            new=AsyncMock(return_value=partial_crawl),
        ), patch(
            "backend.app.agent.nodes.business_overview_node.generate_business_overview",
            new=AsyncMock(return_value=overview_result),
        ):
            final_state = await graph.ainvoke({"business_form": business_form})

            self.assertEqual(final_state.get("workflow_status"), "completed")
            self.assertEqual(len(final_state.get("scraped_pages", [])), 2)
            self.assertEqual(len(final_state.get("failed_pages", [])), 1)
            self.assertIsNotNone(final_state.get("business_overview"))
            # Advisory recorded in errors
            self.assertTrue(
                any("Website crawling advisory" in err for err in final_state.get("errors", []))
            )

    async def test_failure_url_discovery_returns_no_candidates(self):
        """Verify failure cascade when URL discovery yields zero candidate URLs."""
        business_form = _create_sample_business_form()
        empty_discovery = WebsiteURLDiscoveryResult(
            requested_url="https://www.luminabotanicals.com",
            base_url="https://www.luminabotanicals.com",
            status="failed",
            homepage_status="failed",
            sitemap_status="no_sitemap_found",
            candidate_urls=[],
            total_candidates=0,
        )

        with patch(
            "backend.app.agent.nodes.url_discovery_node.discover_website_urls",
            new=AsyncMock(return_value=empty_discovery),
        ):
            final_state = await graph.ainvoke({"business_form": business_form})

            # Workflow marks failed
            self.assertEqual(final_state.get("workflow_status"), "failed")
            self.assertEqual(final_state.get("candidate_urls"), [])
            self.assertEqual(final_state.get("selected_pages"), [])
            self.assertEqual(final_state.get("scraped_pages"), [])
            self.assertIsNone(final_state.get("business_overview"))
            self.assertIsNotNone(final_state.get("error_message"))
            self.assertTrue(len(final_state.get("errors", [])) >= 1)

    async def test_failure_page_selection_returns_no_pages(self):
        """Verify failure cascade when page selection yields zero selected pages."""
        business_form = _create_sample_business_form()
        discovery_result, _ = _create_sample_discovery_result()

        empty_selection = PageSelectionResult(
            selected_pages=[],
            total_selected=0,
            selection_method="deterministic_fallback",
            error_message="No suitable pages could be selected from candidates.",
        )

        with patch(
            "backend.app.agent.nodes.url_discovery_node.discover_website_urls",
            new=AsyncMock(return_value=discovery_result),
        ), patch(
            "backend.app.agent.nodes.page_selection_node.select_pages_with_llm",
            new=AsyncMock(return_value=empty_selection),
        ):
            final_state = await graph.ainvoke({"business_form": business_form})

            self.assertEqual(final_state.get("workflow_status"), "failed")
            self.assertEqual(len(final_state.get("candidate_urls", [])), 5)
            self.assertEqual(final_state.get("selected_pages"), [])
            self.assertEqual(final_state.get("scraped_pages"), [])
            self.assertIsNone(final_state.get("business_overview"))
            self.assertTrue(
                any("Page selection" in err for err in final_state.get("errors", []))
            )
            self.assertIsNotNone(final_state.get("selection_result"))
            self.assertEqual(final_state["selection_result"].total_selected, 0)

    async def test_failure_crawling_produces_no_usable_content(self):
        """Verify failure cascade when website crawling yields zero usable scraped pages."""
        business_form = _create_sample_business_form()
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
            final_state = await graph.ainvoke({"business_form": business_form})

            self.assertEqual(final_state.get("workflow_status"), "failed")
            self.assertEqual(len(final_state.get("selected_pages", [])), 3)
            self.assertEqual(final_state.get("scraped_pages"), [])
            self.assertEqual(len(final_state.get("failed_pages", [])), 3)
            self.assertIsNone(final_state.get("business_overview"))

    async def test_failure_business_overview_raises_exception(self):
        """Verify error handling when business overview service raises an unexpected exception."""
        business_form = _create_sample_business_form()
        discovery_result, _ = _create_sample_discovery_result()
        selection_result, _ = _create_sample_selection_result()
        crawl_result, _, _ = _create_sample_crawl_result()

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
            new=AsyncMock(side_effect=RuntimeError("OpenAI API gateway timeout")),
        ):
            final_state = await graph.ainvoke({"business_form": business_form})

            self.assertEqual(final_state.get("workflow_status"), "failed")
            self.assertIsNone(final_state.get("business_overview"))
            self.assertIsNotNone(final_state.get("overview_result"))
            self.assertEqual(final_state["overview_result"].status, "failed")
            self.assertIn(
                "OpenAI API gateway timeout", final_state.get("error_message", "")
            )

    async def test_failure_missing_initial_business_form(self):
        """Verify workflow handles missing business_form safely at the entry node."""
        initial_state: AgentState = {"business_form": None}
        final_state = await graph.ainvoke(initial_state)

        self.assertEqual(final_state.get("workflow_status"), "failed")
        self.assertEqual(final_state.get("candidate_urls"), [])
        self.assertIsNone(final_state.get("business_overview"))
        self.assertIn("business_form", final_state.get("error_message", ""))


if __name__ == "__main__":
    unittest.main()
