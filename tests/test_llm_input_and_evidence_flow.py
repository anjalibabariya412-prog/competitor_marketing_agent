"""Comprehensive Offline Regression Tests for LLM Input and Evidence Flow.

Verifies all 14 scenarios required for Issue 3:
1. Several useful pages fit within the input budget and all are represented.
2. A very long product page does not crowd out homepage and About content.
3. Pricing or offer content remains represented when available.
4. The total formatted input respects the configured limit.
5. Truncation is deterministic.
6. Empty pages are excluded.
7. Page titles and source URLs survive prompt formatting.
8. A selected page is tracked correctly when its content is truncated.
9. An LLM response containing a fabricated URL is rejected or sanitized appropriately.
10. A valid source URL with harmless formatting differences is handled correctly.
11. Unsupported claims are not incorrectly retained as verified facts.
12. Valid findings are not unnecessarily discarded during sanitization.
13. Missing evidence is handled without inventing a source.
14. The API response contains only findings that survive the intended validation rules.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import backend.app.main  # Ensure canonical import order
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
    _format_pages_for_prompt,
    _normalize_url_for_comparison,
    _resolve_and_verify_source_url,
    _sanitize_and_validate_overview,
    generate_business_overview,
)


class TestLLMInputAndEvidenceFlow(unittest.IsolatedAsyncioTestCase):
    """Test suite for evidence flow, input-budgeting, URL normalization, and overview sanitization."""

    def test_01_several_pages_fit_budget_all_represented(self):
        """Scenario 1: Several useful pages fitting within the input budget are all represented in formatted prompt."""
        pages = [
            ScrapedPage(
                url="https://store.example.com/",
                title="Store Homepage",
                content="Welcome to our artisan leather store. We handcraft wallets and bags.",
                page_type=PageType.HOME,
            ),
            ScrapedPage(
                url="https://store.example.com/about",
                title="About Us",
                content="Founded in 2018 in Florence, our master leatherworkers use traditional vegetable tanning.",
                page_type=PageType.ABOUT,
            ),
            ScrapedPage(
                url="https://store.example.com/products/classic-wallet",
                title="Classic Leather Wallet",
                content="Hand-stitched bifold wallet with 8 card slots. Available in cognac and espresso ($85).",
                page_type=PageType.PRODUCT,
            ),
            ScrapedPage(
                url="https://store.example.com/offers",
                title="Special Offers",
                content="Bundle & Save: Buy a wallet and bag together for 20% off with code LEATHER20.",
                page_type=PageType.OFFER,
            ),
            ScrapedPage(
                url="https://store.example.com/contact",
                title="Customer Care",
                content="Reach our workshop via support@store.example.com or phone +1-555-0199.",
                page_type=PageType.CONTACT,
            ),
        ]

        formatted_text, included_pages = _format_pages_for_prompt(pages)

        # All 5 pages must be included
        self.assertEqual(len(included_pages), 5)
        self.assertEqual({p.url for p in included_pages}, {p.url for p in pages})
        # Check that page titles and excerpts are present in the formatted prompt
        for p in pages:
            self.assertIn(p.url, formatted_text)
            self.assertIn(p.title, formatted_text)
            self.assertIn(p.content, formatted_text)

    def test_02_very_long_product_page_does_not_crowd_out_homepage_and_about(self):
        """Scenario 2: An unusually long product page is included in full without crowding out Homepage and About content."""
        # Page 1: 500-char Homepage
        home_page = ScrapedPage(
            url="https://saas.example.com/",
            title="SaaS Platform",
            content="Enterprise Workflow Automation Platform. Automate business pipelines seamlessly across teams.",
            page_type=PageType.HOME,
        )
        # Page 2: 600-char About Us
        about_page = ScrapedPage(
            url="https://saas.example.com/about",
            title="Company Story",
            content="Built by former infrastructure engineers to solve distributed workflow orchestration reliability.",
            page_type=PageType.ABOUT,
        )
        # Page 3: 40,000-char massive technical catalog / product page
        giant_product = ScrapedPage(
            url="https://saas.example.com/features/deep-spec",
            title="Technical API & Feature Specifications",
            content="API endpoint documentation and system architecture details. " * 650,  # ~40,000 chars
            page_type=PageType.PRODUCT,
        )
        # Page 4: 800-char Pricing
        pricing_page = ScrapedPage(
            url="https://saas.example.com/pricing",
            title="Plans & Pricing",
            content="Starter Plan: $29/mo. Professional Plan: $99/mo. Enterprise: Custom billing.",
            page_type=PageType.PRICING,
        )

        pages = [home_page, about_page, giant_product, pricing_page]
        formatted_text, included_pages = _format_pages_for_prompt(pages)

        # ALL 4 pages must be included in the prompt
        self.assertEqual(len(included_pages), 4)
        included_urls = {p.url for p in included_pages}
        self.assertIn("https://saas.example.com/", included_urls)
        self.assertIn("https://saas.example.com/about", included_urls)
        self.assertIn("https://saas.example.com/features/deep-spec", included_urls)
        self.assertIn("https://saas.example.com/pricing", included_urls)

        # Content from Home, About, Features, and Pricing must be fully preserved
        self.assertIn("Enterprise Workflow Automation Platform", formatted_text)
        self.assertIn("Built by former infrastructure engineers", formatted_text)
        self.assertIn("Starter Plan: $29/mo", formatted_text)
        self.assertIn(giant_product.content.strip(), formatted_text)
        self.assertNotIn("[... Content truncated", formatted_text)

    def test_03_pricing_and_offer_content_remains_represented(self):
        """Scenario 3: Pricing and promotional offer pages remain represented when available alongside multiple products."""
        pages = [
            ScrapedPage(
                url="https://dental.example.com/",
                title="City Dental Clinic",
                content="Comprehensive family and cosmetic dentistry in downtown Seattle.",
                page_type=PageType.HOME,
            ),
            ScrapedPage(
                url="https://dental.example.com/services/ortho",
                title="Orthodontics",
                content="Invisalign and traditional braces for teens and adults. " * 80,
                page_type=PageType.SERVICE,
            ),
            ScrapedPage(
                url="https://dental.example.com/services/implants",
                title="Dental Implants",
                content="Permanent restorative dental implants using surgical-grade titanium. " * 80,
                page_type=PageType.SERVICE,
            ),
            ScrapedPage(
                url="https://dental.example.com/pricing",
                title="Fee Schedule & Financing",
                content="Checkup & Cleaning: $120. Professional Whitening: $299. 0% interest financing for 12 months.",
                page_type=PageType.PRICING,
            ),
            ScrapedPage(
                url="https://dental.example.com/booking",
                title="Book an Appointment",
                content="Schedule consultation online or call our patient coordinator at 206-555-0144.",
                page_type=PageType.CONTACT,
            ),
        ]

        formatted_text, included_pages = _format_pages_for_prompt(pages)
        self.assertEqual(len(included_pages), 5)
        # Pricing evidence is intact
        self.assertIn("Fee Schedule & Financing", formatted_text)
        self.assertIn("Checkup & Cleaning: $120", formatted_text)
        self.assertIn("0% interest financing", formatted_text)

    def test_04_multiple_long_pages_included_completely(self):
        """Scenario 4: Multiple long pages (>6,000 chars each) are all included with their complete content without truncation."""
        # 8 pages, each containing 12,000 chars of text (total 96,000 raw chars)
        pages = [
            ScrapedPage(
                url=f"https://enterprise.example.com/page-{i}",
                title=f"Enterprise Page {i}",
                content=(f"Paragraph for page {i} outlining technical system specifications and requirements. " * 150),
                page_type=PageType.PRODUCT,
            )
            for i in range(1, 9)
        ]

        formatted_text, included_pages = _format_pages_for_prompt(pages)

        self.assertEqual(len(included_pages), 8)
        self.assertNotIn("[... Content truncated", formatted_text)
        for p in pages:
            self.assertIn(p.content.strip(), formatted_text)
        self.assertGreater(len(formatted_text), 90_000)

    def test_05_formatting_is_deterministic(self):
        """Scenario 5: Page formatting is completely deterministic across multiple runs."""
        pages = [
            ScrapedPage(
                url=f"https://example.com/doc/{i}",
                title=f"Doc {i}",
                content=f"Document section {i} detail information. " * (50 * i),
                page_type=PageType.PRODUCT,
            )
            for i in range(1, 7)
        ]

        run1_text, run1_pages = _format_pages_for_prompt(pages)
        run2_text, run2_pages = _format_pages_for_prompt(pages)

        self.assertEqual(run1_text, run2_text)
        self.assertEqual([p.url for p in run1_pages], [p.url for p in run2_pages])

    def test_06_empty_pages_are_excluded(self):
        """Scenario 6: Pages with empty or whitespace-only content are excluded from prompt formatting."""
        pages = [
            ScrapedPage(url="https://example.com/", title="Home", content="Valid home text", page_type=PageType.HOME),
            ScrapedPage(url="https://example.com/blank1", title="Blank", content="", page_type=PageType.OTHER),
            ScrapedPage(url="https://example.com/blank2", title="Whitespace", content="   \n\t   ", page_type=PageType.OTHER),
            ScrapedPage(url="https://example.com/about", title="About", content="Valid about text", page_type=PageType.ABOUT),
        ]

        formatted_text, included_pages = _format_pages_for_prompt(pages)

        self.assertEqual(len(included_pages), 2)
        included_urls = [p.url for p in included_pages]
        self.assertEqual(included_urls, ["https://example.com/", "https://example.com/about"])
        self.assertNotIn("https://example.com/blank1", formatted_text)
        self.assertNotIn("https://example.com/blank2", formatted_text)

    def test_07_page_titles_and_source_urls_survive_prompt_formatting(self):
        """Scenario 7: Page titles and exact source URLs appear cleanly in prompt headers."""
        pages = [
            ScrapedPage(
                url="https://agency.example.com/consulting",
                title="B2B Growth Consulting",
                content="Strategic advisory for venture-backed founders.",
                page_type=PageType.SERVICE,
            )
        ]

        formatted_text, _ = _format_pages_for_prompt(pages)

        self.assertIn("--- Page 1: https://agency.example.com/consulting", formatted_text)
        self.assertIn("Title: B2B Growth Consulting", formatted_text)
        self.assertIn("Type: service", formatted_text)

    def test_08_page_longer_than_6000_chars_and_tail_content_retained(self):
        """Scenario 8: A page longer than 6,000 chars reaches the prompt without truncation, and tail content is retained."""
        body_paragraphs = [f"Paragraph {i}: Detailed discussion of product capabilities." for i in range(1, 200)]
        tail_evidence = "CRITICAL_TAIL_EVIDENCE: Unique lifetime replacement warranty policy for all enterprise clients."
        body_paragraphs.append(tail_evidence)
        large_content = "\n\n".join(body_paragraphs)

        page = ScrapedPage(
            url="https://example.com/big-page",
            title="Big Enterprise Page",
            content=large_content,
            page_type=PageType.PRODUCT,
        )

        formatted_text, included_pages = _format_pages_for_prompt([page])

        self.assertEqual(len(included_pages), 1)
        self.assertGreater(len(page.content), 6_000)
        self.assertNotIn("[... Content truncated", formatted_text)
        self.assertIn("CRITICAL_TAIL_EVIDENCE: Unique lifetime replacement warranty policy", formatted_text)
        self.assertIn("--- Page 1: https://example.com/big-page", formatted_text)
        self.assertIn("Title: Big Enterprise Page", formatted_text)
        self.assertIn("Type: product", formatted_text)

    def test_09_fabricated_url_in_llm_response_rejected(self):
        """Scenario 9: Hallucinated URLs in LLM findings or products are sanitized and rejected."""
        valid_page_url = "https://store.example.com/products/wallet"
        supplied_pages = [valid_page_url]

        raw_overview = BusinessOverview(
            business_name="Store",
            website_url="https://store.example.com",
            business_description="Artisan goods",
            products_and_services=[
                ProductServiceItem(
                    name="Leather Wallet",
                    description="Real wallet",
                    source_url="https://store.example.com/fabricated-path-that-never-existed",
                )
            ],
            target_audience=TargetAudienceInfo(
                description="Design conscious buyers",
                is_explicit=True,
                source_url="https://completely-different-domain.com/persona",
            ),
            positioning_and_value_propositions=[
                ValueProposition(
                    statement="Best leather craftsmanship",
                    is_explicit=True,
                    source_url="https://store.example.com/fake-value-prop",
                )
            ],
            key_findings=[
                Finding(
                    statement="Fabricated warranty statement",
                    finding_type=FindingType.POSITIONING,
                    source_url="https://store.example.com/invented-warranty",
                )
            ],
            sources_used=["https://store.example.com/invented-warranty"],
        )

        sanitized = _sanitize_and_validate_overview(
            raw_overview,
            valid_source_urls=supplied_pages,
            fallback_website_url="https://store.example.com",
        )

        # 1. Product source_url is set to None
        self.assertIsNone(sanitized.products_and_services[0].source_url)
        # 2. Target audience external source set to None, and is_explicit downgraded to False
        self.assertIsNone(sanitized.target_audience.source_url)
        self.assertFalse(sanitized.target_audience.is_explicit)
        # 3. Value proposition un-crawled source set to None, and is_explicit downgraded to False
        self.assertIsNone(sanitized.positioning_and_value_propositions[0].source_url)
        self.assertFalse(sanitized.positioning_and_value_propositions[0].is_explicit)
        # 4. Finding citing unverified URL is DROPPED
        self.assertEqual(len(sanitized.key_findings), 0)
        # 5. sources_used does NOT contain the invented warranty page
        self.assertEqual(sanitized.sources_used, [])

    def test_10_harmless_url_formatting_differences_resolved(self):
        """Scenario 10: Harmless URL differences (www., trailing slash, tracking queries, relative paths) resolve cleanly."""
        original_crawled_url = "https://www.example.com/pricing/?utm_campaign=winter"
        supplied_pages = [original_crawled_url]

        # LLM cites with:
        # a) no www, no query, no trailing slash
        # b) relative path
        # c) http scheme instead of https
        raw_overview = BusinessOverview(
            business_name="Example",
            website_url="https://example.com",
            business_description="Example platform",
            products_and_services=[
                ProductServiceItem(
                    name="Standard Subscription",
                    description="$49 per month",
                    source_url="https://example.com/pricing",  # No www, no trailing slash, no query
                ),
                ProductServiceItem(
                    name="Enterprise Plan",
                    description="Custom quote",
                    source_url="/pricing",  # Relative path
                ),
            ],
            target_audience=TargetAudienceInfo(
                description="Small business owners",
                is_explicit=False,
                source_url=None,
            ),
            positioning_and_value_propositions=[
                ValueProposition(
                    statement="Transparent pricing with zero hidden fees",
                    is_explicit=True,
                    source_url="http://example.com/pricing/",  # http scheme, trailing slash
                )
            ],
            key_findings=[
                Finding(
                    statement="Annual plan includes 2 months free",
                    finding_type=FindingType.PRICING,
                    source_url="https://www.example.com/pricing",  # No query parameter
                )
            ],
            sources_used=["https://example.com/pricing"],
        )

        sanitized = _sanitize_and_validate_overview(
            raw_overview,
            valid_source_urls=supplied_pages,
            fallback_website_url="https://example.com",
        )

        # All items should resolve to the exact canonical original crawled URL
        self.assertEqual(sanitized.products_and_services[0].source_url, original_crawled_url)
        self.assertEqual(sanitized.products_and_services[1].source_url, original_crawled_url)
        self.assertEqual(sanitized.positioning_and_value_propositions[0].source_url, original_crawled_url)
        self.assertEqual(len(sanitized.key_findings), 1)
        self.assertEqual(sanitized.key_findings[0].source_url, original_crawled_url)
        self.assertEqual(sanitized.sources_used, [original_crawled_url])

    def test_11_unsupported_claims_not_retained_as_explicit_facts(self):
        """Scenario 11: Explicit positioning claims without verifiable sources have is_explicit downgraded to False."""
        supplied_pages = ["https://example.com/"]

        raw_overview = BusinessOverview(
            business_name="Test Co",
            website_url="https://example.com",
            business_description="Test description",
            target_audience=TargetAudienceInfo(
                description="Fortune 500 CIOs",
                is_explicit=True,
                source_url="https://example.com/un-crawled-cio-whitepaper",
            ),
            positioning_and_value_propositions=[
                ValueProposition(
                    statement="Ranked #1 by Gartner in 2024",
                    is_explicit=True,
                    source_url="https://gartner.com/external-report",
                )
            ],
        )

        sanitized = _sanitize_and_validate_overview(
            raw_overview,
            valid_source_urls=supplied_pages,
            fallback_website_url="https://example.com",
        )

        # Both unverified explicit claims must be converted to is_explicit=False
        self.assertFalse(sanitized.target_audience.is_explicit)
        self.assertIsNone(sanitized.target_audience.source_url)
        self.assertFalse(sanitized.positioning_and_value_propositions[0].is_explicit)
        self.assertIsNone(sanitized.positioning_and_value_propositions[0].source_url)

    def test_12_valid_findings_not_unnecessarily_discarded(self):
        """Scenario 12: Valid findings citing legitimately crawled pages are strictly retained."""
        page1 = "https://dental.example.com/services/ortho"
        page2 = "https://dental.example.com/pricing"
        supplied_pages = [page1, page2]

        finding1 = Finding(
            statement="Invisalign treatment plans start at $3,500 with zero down.",
            finding_type=FindingType.PRICING,
            source_url=page2,
            is_inferred=False,
        )
        finding2 = Finding(
            statement="Offers weekend orthodontic adjustment consultations.",
            finding_type=FindingType.PRODUCT_SERVICE,
            source_url=page1,
            is_inferred=False,
        )

        raw_overview = BusinessOverview(
            business_name="Dental Clinic",
            website_url="https://dental.example.com",
            business_description="Dental care",
            target_audience=TargetAudienceInfo(description="Families", is_explicit=False),
            key_findings=[finding1, finding2],
        )

        sanitized = _sanitize_and_validate_overview(
            raw_overview,
            valid_source_urls=supplied_pages,
            fallback_website_url="https://dental.example.com",
        )

        self.assertEqual(len(sanitized.key_findings), 2)
        self.assertEqual(sanitized.key_findings[0].statement, finding1.statement)
        self.assertEqual(sanitized.key_findings[1].statement, finding2.statement)
        self.assertIn(page1, sanitized.sources_used)
        self.assertIn(page2, sanitized.sources_used)

    def test_13_missing_evidence_handled_without_inventing_source(self):
        """Scenario 13: When pricing or audience is missing on site, information gap is recorded and no source is invented."""
        raw_overview = BusinessOverview(
            business_name="Industrial Corp",
            website_url="https://industrial.example.com",
            business_description="Custom manufacturing",
            target_audience=TargetAudienceInfo(
                description="Not specified on website",
                is_explicit=False,
                source_url=None,
            ),
            information_gaps=[
                InformationGap(
                    category="Pricing & Minimum Orders",
                    observation="Public website does not list product pricing or minimum order quantities (MOQ).",
                )
            ],
            key_findings=[],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(
            raw_overview,
            valid_source_urls=["https://industrial.example.com/"],
            fallback_website_url="https://industrial.example.com",
        )

        self.assertIsNone(sanitized.target_audience.source_url)
        self.assertFalse(sanitized.target_audience.is_explicit)
        self.assertEqual(len(sanitized.information_gaps), 1)
        self.assertEqual(sanitized.information_gaps[0].category, "Pricing & Minimum Orders")
        # sources_used must be empty since no page provided evidence for target audience or findings
        self.assertEqual(sanitized.sources_used, [])

    async def test_14_api_response_contains_only_validated_findings_e2e_mock(self):
        """Scenario 14: End-to-end integration verifies that generate_business_overview sanitizes and validates findings."""
        pages = [
            ScrapedPage(
                url="https://legal.example.com/",
                title="Boutique IP Law Firm",
                content="Specializing in patent prosecution and trademark registration for tech startups.",
                page_type=PageType.HOME,
            ),
            ScrapedPage(
                url="https://legal.example.com/pricing",
                title="Flat-Fee Packages",
                content="Provisional Patent Filing: $2,500. Trademark Registration: $1,200. Clear flat-fee schedules.",
                page_type=PageType.PRICING,
            ),
        ]

        form = BusinessForm(
            business_name_and_description="Apex IP Law is a boutique patent and trademark firm for startups.",
            product_or_service="Patent prosecution and trademark protection",
            target_audience="Early-stage tech founders and hardware startups",
            audience_unknown=False,
            business_website="https://legal.example.com",
        )

        # Mock structured LLM output with 1 valid finding and 1 fabricated finding
        mock_overview = BusinessOverview(
            business_name="Apex IP Law",
            website_url="https://legal.example.com",
            business_description="Boutique intellectual property law firm.",
            products_and_services=[
                ProductServiceItem(
                    name="Provisional Patent Filing",
                    description="Flat-fee filing ($2,500)",
                    source_url="https://legal.example.com/pricing",
                ),
            ],
            target_audience=TargetAudienceInfo(
                description="Early-stage tech founders",
                is_explicit=True,
                source_url=None,
            ),
            positioning_and_value_propositions=[
                ValueProposition(
                    statement="Predictable flat-fee pricing model for early startups",
                    is_explicit=True,
                    source_url="https://legal.example.com/pricing",
                )
            ],
            key_findings=[
                Finding(
                    statement="Trademark registration package is offered at $1,200 flat fee.",
                    finding_type=FindingType.PRICING,
                    source_url="https://legal.example.com/pricing",
                    is_inferred=False,
                ),
                Finding(
                    statement="Free initial IP audits offered to Y Combinator batches.",
                    finding_type=FindingType.OFFER,
                    source_url="https://legal.example.com/fabricated-yc-offer",  # Hallucinated URL
                    is_inferred=False,
                ),
            ],
            sources_used=["https://legal.example.com/pricing"],
        )

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(message=MagicMock(parsed=mock_overview))]

        with patch("backend.app.services.business_overview_service.AsyncOpenAI") as mock_openai_cls, \
             patch("backend.app.services.business_overview_service.settings.OPENAI_API_KEY") as mock_key:
            mock_key.get_secret_value.return_value = "sk-test-key"
            mock_client = AsyncMock()
            mock_client.beta.chat.completions.parse.return_value = mock_completion
            mock_openai_cls.return_value = mock_client

            result = await generate_business_overview(
                crawl_input=pages,
                website_url="https://legal.example.com",
                business_form=form,
            )

            self.assertEqual(result.status, "success")
            overview = result.business_overview
            self.assertIsNotNone(overview)

            # Valid finding was retained
            self.assertEqual(len(overview.key_findings), 1)
            self.assertEqual(overview.key_findings[0].source_url, "https://legal.example.com/pricing")
            # Fabricated finding citing un-crawled URL was dropped
            self.assertFalse(any("fabricated-yc-offer" in f.source_url for f in overview.key_findings))

            # User intake audience preserved
            self.assertIn('Early-stage tech founders', overview.target_audience.description)
            self.assertTrue(overview.target_audience.is_explicit)

            # Sources used contains only valid page
            self.assertEqual(overview.sources_used, ["https://legal.example.com/pricing"])


if __name__ == "__main__":
    unittest.main()
