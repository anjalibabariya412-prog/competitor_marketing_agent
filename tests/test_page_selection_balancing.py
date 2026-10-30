"""Comprehensive Offline Regression Tests for Balanced Website Page Selection.

Verifies all 12 key scenarios required for balanced page selection:
1. E-commerce website with homepage, About, category, product-detail, pricing, and policy pages.
2. Service business with service, pricing, booking, About, and contact pages.
3. SaaS website with product features, plans/pricing, use cases, and About pages.
4. Small static website with only a few available pages.
5. Many similar product pages competing for a limited selection budget.
6. No pricing page available.
7. LLM returns an incomplete or unbalanced selection.
8. LLM returns invalid URLs or duplicate URLs.
9. LLM selection fails and deterministic fallback is used.
10. Product/service relevance must coexist with business-context coverage.
11. Discovered candidates contain no About page or no homepage.
12. The selected URLs remain within the configured maximum page limit.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import backend.app.main  # Ensure canonical import order
from backend.app.schemas.enums import PageType
from backend.app.services.page_selection_service import (
    PageSelectionDecision,
    StructuredPageSelectionResponse,
    categorize_candidate,
    select_pages_deterministic,
    select_pages_with_llm,
    SelectionCategory,
)
from backend.app.services.url_extractor_service import CandidateURL


class TestPageSelectionBalancing(unittest.IsolatedAsyncioTestCase):
    """Test suite verifying balanced page selection across website types and failure modes."""

    def test_01_ecommerce_balanced_selection(self):
        """Scenario 1: E-commerce site selects balanced set of Home, About, Collection, Products, Offers, Policy."""
        candidates = [
            CandidateURL(url="https://store.example.com/", link_text="Home", page_type=PageType.HOME, source_section="nav"),
            CandidateURL(url="https://store.example.com/about-us", link_text="About Us", page_type=PageType.ABOUT, source_section="nav"),
            CandidateURL(url="https://store.example.com/collections/summer-apparel", link_text="Summer Apparel", page_type=PageType.PRODUCT, source_section="nav"),
            CandidateURL(url="https://store.example.com/products/linen-shirt", link_text="Linen Shirt", page_type=PageType.PRODUCT, source_section="content"),
            CandidateURL(url="https://store.example.com/products/linen-shorts", link_text="Linen Shorts", page_type=PageType.PRODUCT, source_section="content"),
            CandidateURL(url="https://store.example.com/products/summer-dress", link_text="Summer Dress", page_type=PageType.PRODUCT, source_section="content"),
            CandidateURL(url="https://store.example.com/offers/summer-sale", link_text="Summer Sale - 30% Off", page_type=PageType.OFFER, source_section="nav"),
            CandidateURL(url="https://store.example.com/policies/shipping-and-returns", link_text="Shipping & Returns", page_type=PageType.POLICY, source_section="footer"),
            CandidateURL(url="https://store.example.com/contact", link_text="Contact Customer Support", page_type=PageType.CONTACT, source_section="footer"),
        ]

        result = select_pages_deterministic(candidates, max_pages=8, base_url="https://store.example.com")
        selected_urls = [p.url for p in result.selected_pages]

        self.assertLessEqual(len(selected_urls), 8)
        self.assertIn("https://store.example.com/", selected_urls, "Homepage must be selected")
        self.assertIn("https://store.example.com/about-us", selected_urls, "About page must be selected")
        self.assertIn("https://store.example.com/collections/summer-apparel", selected_urls, "Collection category must be selected")
        self.assertIn("https://store.example.com/offers/summer-sale", selected_urls, "Offers/Sale page must be selected")
        self.assertIn("https://store.example.com/policies/shipping-and-returns", selected_urls, "Shipping/Returns policy must be selected")
        self.assertIn("https://store.example.com/contact", selected_urls, "Contact page must be selected")

    def test_02_service_business_balanced_selection(self):
        """Scenario 2: Service business selects services, pricing, booking, About, and contact."""
        candidates = [
            CandidateURL(url="https://clinic.example.com/", link_text="Home", page_type=PageType.HOME, source_section="nav"),
            CandidateURL(url="https://clinic.example.com/about-our-doctors", link_text="About Our Specialists", page_type=PageType.ABOUT, source_section="nav"),
            CandidateURL(url="https://clinic.example.com/services", link_text="Our Clinical Services", page_type=PageType.SERVICE, source_section="nav"),
            CandidateURL(url="https://clinic.example.com/services/orthodontics", link_text="Orthodontic Treatment", page_type=PageType.SERVICE, source_section="nav"),
            CandidateURL(url="https://clinic.example.com/pricing-and-fees", link_text="Treatment Pricing & Insurance", page_type=PageType.PRICING, source_section="nav"),
            CandidateURL(url="https://clinic.example.com/book-appointment", link_text="Book an Appointment", page_type=PageType.CONTACT, source_section="header"),
            CandidateURL(url="https://clinic.example.com/contact-us", link_text="Contact & Clinic Location", page_type=PageType.CONTACT, source_section="footer"),
            CandidateURL(url="https://clinic.example.com/faq", link_text="Patient FAQ", page_type=PageType.FAQ, source_section="footer"),
        ]

        result = select_pages_deterministic(candidates, max_pages=6, base_url="https://clinic.example.com")
        selected_urls = [p.url for p in result.selected_pages]

        self.assertLessEqual(len(selected_urls), 6)
        self.assertIn("https://clinic.example.com/", selected_urls)
        self.assertIn("https://clinic.example.com/about-our-doctors", selected_urls)
        self.assertIn("https://clinic.example.com/pricing-and-fees", selected_urls)
        self.assertIn("https://clinic.example.com/book-appointment", selected_urls)
        self.assertTrue(any("/services" in u for u in selected_urls))

    def test_03_saas_business_balanced_selection(self):
        """Scenario 3: SaaS website selects product features, plans/pricing, use cases, and About."""
        candidates = [
            CandidateURL(url="https://cloudapp.example.com/", link_text="Home", page_type=PageType.HOME, source_section="nav"),
            CandidateURL(url="https://cloudapp.example.com/company/about", link_text="About Us", page_type=PageType.ABOUT, source_section="nav"),
            CandidateURL(url="https://cloudapp.example.com/features", link_text="Product Features & Capabilities", page_type=PageType.PRODUCT, source_section="nav"),
            CandidateURL(url="https://cloudapp.example.com/pricing", link_text="Plans & Pricing Tiers", page_type=PageType.PRICING, source_section="nav"),
            CandidateURL(url="https://cloudapp.example.com/use-cases/enterprise", link_text="Enterprise Solutions", page_type=PageType.SERVICE, source_section="nav"),
            CandidateURL(url="https://cloudapp.example.com/request-demo", link_text="Schedule a Live Demo", page_type=PageType.CONTACT, source_section="header"),
            CandidateURL(url="https://cloudapp.example.com/customers/case-studies", link_text="Customer Case Studies", page_type=PageType.OTHER, source_section="nav"),
        ]

        result = select_pages_deterministic(candidates, max_pages=6, base_url="https://cloudapp.example.com")
        selected_urls = [p.url for p in result.selected_pages]

        self.assertIn("https://cloudapp.example.com/", selected_urls)
        self.assertIn("https://cloudapp.example.com/company/about", selected_urls)
        self.assertIn("https://cloudapp.example.com/features", selected_urls)
        self.assertIn("https://cloudapp.example.com/pricing", selected_urls)
        self.assertIn("https://cloudapp.example.com/request-demo", selected_urls)

    def test_04_small_static_website_selection(self):
        """Scenario 4: Small static website with only 3 pages selects all 3 cleanly without errors."""
        candidates = [
            CandidateURL(url="https://boutique.example.com/", link_text="Home", page_type=PageType.HOME, source_section="nav"),
            CandidateURL(url="https://boutique.example.com/about", link_text="About", page_type=PageType.ABOUT, source_section="nav"),
            CandidateURL(url="https://boutique.example.com/contact", link_text="Contact", page_type=PageType.CONTACT, source_section="nav"),
        ]

        result = select_pages_deterministic(candidates, max_pages=8, base_url="https://boutique.example.com")
        self.assertEqual(result.total_selected, 3)
        selected_urls = [p.url for p in result.selected_pages]
        self.assertEqual(set(selected_urls), {c.url for c in candidates})

    def test_05_many_similar_product_pages_do_not_crowd_out_other_categories(self):
        """Scenario 5: 25 similar product variants do not consume the budget; Home, About, Offers, Contact survive."""
        candidates = [
            CandidateURL(url="https://store.example.com/", link_text="Home", page_type=PageType.HOME, source_section="nav"),
            CandidateURL(url="https://store.example.com/about", link_text="About Us", page_type=PageType.ABOUT, source_section="nav"),
            CandidateURL(url="https://store.example.com/offers", link_text="Special Offers", page_type=PageType.OFFER, source_section="nav"),
            CandidateURL(url="https://store.example.com/contact", link_text="Contact Us", page_type=PageType.CONTACT, source_section="footer"),
        ]
        # Add 25 product variant URLs
        for i in range(1, 26):
            candidates.append(
                CandidateURL(
                    url=f"https://store.example.com/products/lipstick-shade-{i:02d}",
                    link_text=f"Velvet Lipstick Shade {i:02d}",
                    page_type=PageType.PRODUCT,
                    source_section="content",
                )
            )

        result = select_pages_deterministic(candidates, max_pages=6, base_url="https://store.example.com")
        selected_urls = [p.url for p in result.selected_pages]

        self.assertEqual(len(selected_urls), 6)
        # Verify core business context pages are NOT crowded out by the 25 lipstick variants
        self.assertIn("https://store.example.com/", selected_urls)
        self.assertIn("https://store.example.com/about", selected_urls)
        self.assertIn("https://store.example.com/offers", selected_urls)
        self.assertIn("https://store.example.com/contact", selected_urls)

        # Only 2 product slots should be consumed out of 6
        product_count = sum(1 for u in selected_urls if "/products/" in u)
        self.assertEqual(product_count, 2)

    def test_06_no_pricing_page_available_fills_slots_with_other_relevant_pages(self):
        """Scenario 6: When no pricing page exists, slots naturally flow to other informative pages without fabrication."""
        candidates = [
            CandidateURL(url="https://agency.example.com/", link_text="Home", page_type=PageType.HOME, source_section="nav"),
            CandidateURL(url="https://agency.example.com/about", link_text="About Us", page_type=PageType.ABOUT, source_section="nav"),
            CandidateURL(url="https://agency.example.com/services/seo", link_text="SEO Services", page_type=PageType.SERVICE, source_section="nav"),
            CandidateURL(url="https://agency.example.com/services/ppc", link_text="PPC Advertising", page_type=PageType.SERVICE, source_section="nav"),
            CandidateURL(url="https://agency.example.com/services/content", link_text="Content Strategy", page_type=PageType.SERVICE, source_section="nav"),
            CandidateURL(url="https://agency.example.com/contact", link_text="Contact Us", page_type=PageType.CONTACT, source_section="nav"),
            CandidateURL(url="https://agency.example.com/faq", link_text="Client FAQs", page_type=PageType.FAQ, source_section="footer"),
        ]

        result = select_pages_deterministic(candidates, max_pages=6, base_url="https://agency.example.com")
        self.assertEqual(result.total_selected, 6)
        selected_urls = [p.url for p in result.selected_pages]

        # Verify no fake pricing URL was invented
        self.assertFalse(any("pricing" in u for u in selected_urls))
        # Slots filled with Home, About, Services, Contact, FAQ
        self.assertIn("https://agency.example.com/", selected_urls)
        self.assertIn("https://agency.example.com/about", selected_urls)
        self.assertIn("https://agency.example.com/contact", selected_urls)

    async def test_07_llm_incomplete_selection_backfilled_with_balanced_candidates(self):
        """Scenario 7: When LLM returns an incomplete selection (e.g. only 2 products), remaining slots are backfilled."""
        candidates = [
            CandidateURL(url="https://store.example.com/", link_text="Home", page_type=PageType.HOME, source_section="nav"),
            CandidateURL(url="https://store.example.com/about", link_text="About", page_type=PageType.ABOUT, source_section="nav"),
            CandidateURL(url="https://store.example.com/pricing", link_text="Pricing", page_type=PageType.PRICING, source_section="nav"),
            CandidateURL(url="https://store.example.com/contact", link_text="Contact", page_type=PageType.CONTACT, source_section="nav"),
            CandidateURL(url="https://store.example.com/products/a", link_text="Product A", page_type=PageType.PRODUCT, source_section="content"),
            CandidateURL(url="https://store.example.com/products/b", link_text="Product B", page_type=PageType.PRODUCT, source_section="content"),
        ]

        # Simulate LLM returning only 2 product URLs
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    parsed=StructuredPageSelectionResponse(
                        selected_pages=[
                            PageSelectionDecision(url="https://store.example.com/products/a", reason="Featured product"),
                            PageSelectionDecision(url="https://store.example.com/products/b", reason="Secondary product"),
                        ]
                    )
                )
            )
        ]

        with patch("backend.app.services.page_selection_service.AsyncOpenAI") as mock_openai_cls, \
             patch("backend.app.services.page_selection_service.settings.OPENAI_API_KEY") as mock_key:
            mock_key.get_secret_value.return_value = "sk-test-valid-key"
            mock_client = AsyncMock()
            mock_client.beta.chat.completions.parse.return_value = mock_response
            mock_openai_cls.return_value = mock_client

            result = await select_pages_with_llm(
                candidates,
                website_url="https://store.example.com",
                max_pages=5,
            )

            self.assertEqual(result.total_selected, 5)
            selected_urls = [p.url for p in result.selected_pages]
            # Homepage was prepended
            self.assertEqual(selected_urls[0], "https://store.example.com/")
            # Products from LLM are present
            self.assertIn("https://store.example.com/products/a", selected_urls)
            self.assertIn("https://store.example.com/products/b", selected_urls)
            # About or Pricing backfilled
            self.assertTrue(any(u in selected_urls for u in ("https://store.example.com/about", "https://store.example.com/pricing")))

    async def test_08_llm_invalid_and_duplicate_urls_pruned_and_backfilled(self):
        """Scenario 8: Hallucinated URLs and duplicates from LLM are pruned, and slots backfilled with valid candidates."""
        candidates = [
            CandidateURL(url="https://store.example.com/", link_text="Home", page_type=PageType.HOME, source_section="nav"),
            CandidateURL(url="https://store.example.com/about", link_text="About", page_type=PageType.ABOUT, source_section="nav"),
            CandidateURL(url="https://store.example.com/pricing", link_text="Pricing", page_type=PageType.PRICING, source_section="nav"),
            CandidateURL(url="https://store.example.com/products/item-1", link_text="Item 1", page_type=PageType.PRODUCT, source_section="nav"),
        ]

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    parsed=StructuredPageSelectionResponse(
                        selected_pages=[
                            PageSelectionDecision(url="https://store.example.com/products/item-1", reason="Valid item"),
                            PageSelectionDecision(url="https://store.example.com/products/item-1", reason="Duplicate item"),
                            PageSelectionDecision(url="https://store.example.com/fabricated-url-123", reason="Hallucination"),
                            PageSelectionDecision(url="https://external-site.com/fake", reason="External Hallucination"),
                        ]
                    )
                )
            )
        ]

        with patch("backend.app.services.page_selection_service.AsyncOpenAI") as mock_openai_cls, \
             patch("backend.app.services.page_selection_service.settings.OPENAI_API_KEY") as mock_key:
            mock_key.get_secret_value.return_value = "sk-test-valid-key"
            mock_client = AsyncMock()
            mock_client.beta.chat.completions.parse.return_value = mock_response
            mock_openai_cls.return_value = mock_client

            result = await select_pages_with_llm(
                candidates,
                website_url="https://store.example.com",
                max_pages=4,
                force_llm=True,
            )

            selected_urls = [p.url for p in result.selected_pages]
            # Every selected URL MUST exist in candidates
            candidate_url_set = {c.url for c in candidates}
            for url in selected_urls:
                self.assertIn(url, candidate_url_set, f"Selected URL {url} must exist in candidates")

            # Duplicate item-1 selected only once
            self.assertEqual(selected_urls.count("https://store.example.com/products/item-1"), 1)
            # Hallucinated URLs excluded
            self.assertNotIn("https://store.example.com/fabricated-url-123", selected_urls)
            self.assertNotIn("https://external-site.com/fake", selected_urls)

    async def test_09_llm_failure_uses_deterministic_fallback(self):
        """Scenario 9: When OpenAI call raises an exception, deterministic fallback succeeds with diagnostic info."""
        candidates = [
            CandidateURL(url="https://saas.example.com/", link_text="Home", page_type=PageType.HOME, source_section="nav"),
            CandidateURL(url="https://saas.example.com/pricing", link_text="Pricing", page_type=PageType.PRICING, source_section="nav"),
            CandidateURL(url="https://saas.example.com/about", link_text="About", page_type=PageType.ABOUT, source_section="nav"),
        ]

        with patch("backend.app.services.page_selection_service.AsyncOpenAI") as mock_openai_cls, \
             patch("backend.app.services.page_selection_service.settings.OPENAI_API_KEY") as mock_key:
            mock_key.get_secret_value.return_value = "sk-test-valid-key"
            mock_client = AsyncMock()
            mock_client.beta.chat.completions.parse.side_effect = RuntimeError("OpenAI API rate limit exceeded")
            mock_openai_cls.return_value = mock_client

            result = await select_pages_with_llm(
                candidates,
                website_url="https://saas.example.com",
                max_pages=3,
                force_llm=True,
            )

            self.assertEqual(result.selection_method, "deterministic_fallback")
            self.assertIn("OpenAI call failed", result.error_message)
            self.assertEqual(result.total_selected, 3)
            selected_urls = [p.url for p in result.selected_pages]
            self.assertIn("https://saas.example.com/", selected_urls)
            self.assertIn("https://saas.example.com/pricing", selected_urls)
            self.assertIn("https://saas.example.com/about", selected_urls)

    def test_10_product_relevance_coexists_with_business_context_coverage(self):
        """Scenario 10: Specifying target product prioritizes relevant products without eliminating Home, About, Offers."""
        candidates = [
            CandidateURL(url="https://audio.example.com/", link_text="Home", page_type=PageType.HOME, source_section="nav"),
            CandidateURL(url="https://audio.example.com/about-us", link_text="About Us", page_type=PageType.ABOUT, source_section="nav"),
            CandidateURL(url="https://audio.example.com/promotions", link_text="Special Offers", page_type=PageType.OFFER, source_section="nav"),
            CandidateURL(url="https://audio.example.com/contact", link_text="Contact", page_type=PageType.CONTACT, source_section="nav"),
            CandidateURL(url="https://audio.example.com/collections/headphones", link_text="Headphones Collection", page_type=PageType.PRODUCT, source_section="nav"),
            CandidateURL(url="https://audio.example.com/products/wireless-anc-headphones", link_text="Wireless ANC Headphones", page_type=PageType.PRODUCT, source_section="content"),
            CandidateURL(url="https://audio.example.com/products/phone-case", link_text="Phone Case", page_type=PageType.PRODUCT, source_section="content"),
            CandidateURL(url="https://audio.example.com/products/usb-cable", link_text="USB-C Cable", page_type=PageType.PRODUCT, source_section="content"),
        ]

        result = select_pages_deterministic(
            candidates,
            max_pages=6,
            base_url="https://audio.example.com",
            product_or_service="wireless anc headphones",
        )
        selected_urls = [p.url for p in result.selected_pages]

        # 1. Relevant products prioritized
        self.assertIn("https://audio.example.com/collections/headphones", selected_urls)
        self.assertIn("https://audio.example.com/products/wireless-anc-headphones", selected_urls)
        # 2. Unrelated products do NOT displace relevant ones
        self.assertNotIn("https://audio.example.com/products/phone-case", selected_urls)
        # 3. Essential brand context is preserved
        self.assertIn("https://audio.example.com/", selected_urls)
        self.assertIn("https://audio.example.com/about-us", selected_urls)
        self.assertIn("https://audio.example.com/promotions", selected_urls)

    def test_11_candidates_missing_homepage_or_about_page_handled_safely(self):
        """Scenario 11: Missing homepage or missing About page does not fail; other pages fill the quota."""
        # Subtest A: No About page
        candidates_no_about = [
            CandidateURL(url="https://tech.example.com/", link_text="Home", page_type=PageType.HOME, source_section="nav"),
            CandidateURL(url="https://tech.example.com/services/consulting", link_text="Consulting", page_type=PageType.SERVICE, source_section="nav"),
            CandidateURL(url="https://tech.example.com/pricing", link_text="Pricing", page_type=PageType.PRICING, source_section="nav"),
            CandidateURL(url="https://tech.example.com/contact", link_text="Contact", page_type=PageType.CONTACT, source_section="nav"),
        ]
        res_a = select_pages_deterministic(candidates_no_about, max_pages=4, base_url="https://tech.example.com")
        self.assertEqual(res_a.total_selected, 4)
        urls_a = [p.url for p in res_a.selected_pages]
        self.assertIn("https://tech.example.com/", urls_a)
        self.assertIn("https://tech.example.com/pricing", urls_a)

        # Subtest B: No Homepage candidate
        candidates_no_home = [
            CandidateURL(url="https://tech.example.com/about", link_text="About", page_type=PageType.ABOUT, source_section="nav"),
            CandidateURL(url="https://tech.example.com/services/cloud", link_text="Cloud Solutions", page_type=PageType.SERVICE, source_section="nav"),
            CandidateURL(url="https://tech.example.com/pricing", link_text="Pricing", page_type=PageType.PRICING, source_section="nav"),
            CandidateURL(url="https://tech.example.com/contact", link_text="Contact", page_type=PageType.CONTACT, source_section="nav"),
        ]
        res_b = select_pages_deterministic(candidates_no_home, max_pages=4, base_url="https://tech.example.com")
        self.assertEqual(res_b.total_selected, 4)
        urls_b = [p.url for p in res_b.selected_pages]
        self.assertIn("https://tech.example.com/about", urls_b)
        self.assertIn("https://tech.example.com/pricing", urls_b)

    def test_12_selected_pages_strictly_respect_max_pages_limit(self):
        """Scenario 12: Selected URLs strictly remain within configured max_pages across diverse limits."""
        candidates = [
            CandidateURL(url="https://example.com/", link_text="Home", page_type=PageType.HOME),
            CandidateURL(url="https://example.com/about", link_text="About", page_type=PageType.ABOUT),
            CandidateURL(url="https://example.com/pricing", link_text="Pricing", page_type=PageType.PRICING),
            CandidateURL(url="https://example.com/contact", link_text="Contact", page_type=PageType.CONTACT),
            CandidateURL(url="https://example.com/faq", link_text="FAQ", page_type=PageType.FAQ),
        ]
        for i in range(1, 40):
            candidates.append(CandidateURL(url=f"https://example.com/item-{i}", page_type=PageType.PRODUCT))

        for max_limit in [1, 3, 5, 8, 12]:
            res = select_pages_deterministic(candidates, max_pages=max_limit, base_url="https://example.com")
            self.assertLessEqual(res.total_selected, max_limit)
            self.assertLessEqual(len(res.selected_pages), max_limit)

    async def test_13_deterministic_selection_without_api_key(self):
        """Scenario 13: Deterministic selection functions cleanly when OpenAI API key is missing."""
        candidates = [
            CandidateURL(url="https://shop.example.com/", link_text="Home", page_type=PageType.HOME, source_section="nav"),
            CandidateURL(url="https://shop.example.com/about", link_text="About", page_type=PageType.ABOUT, source_section="nav"),
            CandidateURL(url="https://shop.example.com/pricing", link_text="Plans", page_type=PageType.PRICING, source_section="nav"),
            CandidateURL(url="https://shop.example.com/products/widget", link_text="Widget", page_type=PageType.PRODUCT, source_section="content"),
        ]

        with patch("backend.app.services.page_selection_service.settings.OPENAI_API_KEY", None):
            result = await select_pages_with_llm(
                candidates,
                website_url="https://shop.example.com",
                max_pages=4,
            )
            self.assertEqual(result.selection_method, "deterministic_fallback")
            self.assertEqual(result.total_selected, 4)
            selected_urls = [p.url for p in result.selected_pages]
            self.assertIn("https://shop.example.com/", selected_urls)
            self.assertIn("https://shop.example.com/about", selected_urls)
            self.assertIn("https://shop.example.com/pricing", selected_urls)
            self.assertIn("https://shop.example.com/products/widget", selected_urls)

    async def test_14_ambiguous_selection_triggers_bounded_llm_fallback(self):
        """Scenario 14: Genuinely ambiguous candidates (score tie at cutoff) trigger bounded LLM fallback automatically."""
        # Setup: 6 candidates for 4 slots, where candidates at boundary (3 and 4) have identical scores
        candidates = [
            CandidateURL(url="https://brand.example.com/", link_text="Home", page_type=PageType.HOME, source_section="nav"),
            CandidateURL(url="https://brand.example.com/about-us", link_text="About Us", page_type=PageType.ABOUT, source_section="nav"),
            CandidateURL(url="https://brand.example.com/items/alpha", link_text="Alpha Item", page_type=PageType.PRODUCT, source_section="content"),
            CandidateURL(url="https://brand.example.com/items/beta", link_text="Beta Item", page_type=PageType.PRODUCT, source_section="content"),
            CandidateURL(url="https://brand.example.com/items/gamma", link_text="Gamma Item", page_type=PageType.PRODUCT, source_section="content"),
            CandidateURL(url="https://brand.example.com/items/delta", link_text="Delta Item", page_type=PageType.PRODUCT, source_section="content"),
        ]

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    parsed=StructuredPageSelectionResponse(
                        selected_pages=[
                            PageSelectionDecision(url="https://brand.example.com/", reason="Brand identity"),
                            PageSelectionDecision(url="https://brand.example.com/about-us", reason="Company story"),
                            PageSelectionDecision(url="https://brand.example.com/items/alpha", reason="Flagship product"),
                            PageSelectionDecision(url="https://brand.example.com/items/gamma", reason="Differentiated product"),
                        ]
                    )
                )
            )
        ]

        with patch("backend.app.services.page_selection_service.AsyncOpenAI") as mock_openai_cls, \
             patch("backend.app.services.page_selection_service.settings.OPENAI_API_KEY") as mock_key:
            mock_key.get_secret_value.return_value = "sk-test-valid-key"
            mock_client = AsyncMock()
            mock_client.beta.chat.completions.parse.return_value = mock_response
            mock_openai_cls.return_value = mock_client

            result = await select_pages_with_llm(
                candidates,
                website_url="https://brand.example.com",
                max_pages=4,
            )

            # LLM was invoked due to tie-break ambiguity among product candidates
            self.assertEqual(result.selection_method, "llm")
            self.assertEqual(result.total_selected, 4)
            mock_client.beta.chat.completions.parse.assert_awaited_once()
            # Selected URLs strictly belong to candidates
            candidate_set = {c.url for c in candidates}
            for p in result.selected_pages:
                self.assertIn(p.url, candidate_set)

    async def test_15_confident_selection_skips_llm_call(self):
        """Scenario 15: Confident, unambiguous candidate set resolves deterministically and skips LLM call."""
        candidates = [
            CandidateURL(url="https://saas.example.com/", link_text="Home", page_type=PageType.HOME, source_section="nav"),
            CandidateURL(url="https://saas.example.com/about", link_text="About", page_type=PageType.ABOUT, source_section="nav"),
            CandidateURL(url="https://saas.example.com/pricing", link_text="Pricing", page_type=PageType.PRICING, source_section="nav"),
            CandidateURL(url="https://saas.example.com/contact", link_text="Contact", page_type=PageType.CONTACT, source_section="nav"),
            CandidateURL(url="https://saas.example.com/features", link_text="Core Platform", page_type=PageType.PRODUCT, source_section="nav"),
        ]

        with patch("backend.app.services.page_selection_service.AsyncOpenAI") as mock_openai_cls, \
             patch("backend.app.services.page_selection_service.settings.OPENAI_API_KEY") as mock_key:
            mock_key.get_secret_value.return_value = "sk-test-valid-key"
            mock_client = AsyncMock()
            mock_openai_cls.return_value = mock_client

            result = await select_pages_with_llm(
                candidates,
                website_url="https://saas.example.com",
                max_pages=5,
            )

            # Did NOT call OpenAI because candidate count is within budget and selection is confident
            mock_client.beta.chat.completions.parse.assert_not_called()
            self.assertEqual(result.selection_method, "deterministic_fallback")
            self.assertEqual(result.total_selected, 5)

    def test_16_extended_page_classification_and_categorization(self):
        """Scenario 16: Extended keyword rules classify category, bundles, delivery, and founder pages."""
        from backend.app.services.url_extractor_service import classify_page_type

        cases = [
            ("https://store.example.com/categories/skincare", "Shop Categories", PageType.PRODUCT),
            ("https://store.example.com/bundles/starter-kit", "Value Bundle", PageType.OFFER),
            ("https://store.example.com/delivery-policy", "Delivery & Shipping", PageType.POLICY),
            ("https://store.example.com/our-founder", "Meet Our Founder", PageType.ABOUT),
            ("https://store.example.com/membership", "Join Membership", PageType.PRICING),
            ("https://store.example.com/help-center", "Help Center", PageType.FAQ),
        ]

        for url, text, expected_type in cases:
            pt = classify_page_type(url, text)
            self.assertEqual(pt, expected_type, f"URL {url} should classify as {expected_type}")

    def test_17_sitemap_candidates_merged_without_duplicates(self):
        """Scenario 17: Sitemap candidate merging handles casing and trailing slash normalization without duplicates."""
        from backend.app.services.sitemap_fallback_service import merge_candidate_urls

        homepage_candidates = [
            CandidateURL(url="https://example.com/about", link_text="About Us", page_type=PageType.ABOUT, source_section="nav"),
            CandidateURL(url="https://example.com/products/a", link_text="Product A", page_type=PageType.PRODUCT, source_section="nav"),
        ]
        sitemap_candidates = [
            CandidateURL(url="https://example.com/about/", link_text="", page_type=PageType.ABOUT, source_section="sitemap"),
            CandidateURL(url="https://example.com/Products/A", link_text="", page_type=PageType.PRODUCT, source_section="sitemap"),
            CandidateURL(url="https://example.com/pricing", link_text="", page_type=PageType.PRICING, source_section="sitemap"),
        ]

        merged = merge_candidate_urls(homepage_candidates, sitemap_candidates, base_url="https://example.com")
        self.assertEqual(len(merged), 3)
        urls = [c.url for c in merged]
        self.assertIn("https://example.com/about", urls)
        self.assertIn("https://example.com/products/a", urls)
        self.assertIn("https://example.com/pricing", urls)
        # Ensure homepage candidate metadata was preserved
        about_c = next(c for c in merged if "about" in c.url.lower())
        self.assertEqual(about_c.link_text, "About Us")
        self.assertEqual(about_c.source_section, "nav")


if __name__ == "__main__":
    unittest.main()

