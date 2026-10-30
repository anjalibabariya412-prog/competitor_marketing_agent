"""Comprehensive Offline Regression Tests for Issue 4: Business Overview Schema, Prompts, and Evidence Quality.

Verifies all 18 scenarios required for Issue 4:
1. E-commerce with individual products and collections (differentiation between PRODUCT and COLLECTION).
2. Product variants with different shades, colors, or sizes (variants list on ProductServiceItem).
3. Product with original and discounted prices (PricingInfo with price, original_price, discount_text).
4. Product with an unavailable price (PricingInfo with availability=PriceAvailability.NOT_STATED).
5. Service business with packages or per-session pricing (OfferingType.SERVICE_PACKAGE, billing_period="per session").
6. SaaS business with monthly and annual plans (OfferingType.SUBSCRIPTION_PLAN, billing_period="monthly"/"annual").
7. Business offering a free trial or promotional bundle (trial_or_guarantee="14-day free trial", OfferingType.BUNDLE).
8. Website that does not disclose pricing (availability=PriceAvailability.NOT_STATED or QUOTE_BASED, price=None).
9. Target audience explicitly stated on website (is_explicit=True).
10. Target audience not explicitly stated (is_explicit=False).
11. Unsupported product features or claims handled appropriately.
12. Hallucinated source URLs rejected across products, value propositions, and findings.
13. Valid source URLs with harmless formatting differences resolved.
14. Findings supported by multiple pages (source_urls list populated and verified).
15. Conflicting user-provided and website-derived information (has_conflict=True, documented in conflicts).
16. Missing optional fields handled without error.
17. Existing valid findings surviving sanitization.
18. Final API response conforming to the updated schema.
"""

from __future__ import annotations

import unittest

import backend.app.main  # Ensure canonical import order
from backend.app.schemas.business_form import BusinessForm
from backend.app.schemas.business_overview_response import BusinessOverviewResponse
from backend.app.schemas.enums import FindingType, OfferingType, PageType, PriceAvailability
from backend.app.schemas.scraped_data import Finding, ScrapedPage
from backend.app.services.business_overview_service import (
    BusinessOverview,
    InformationGap,
    PricingInfo,
    ProductServiceItem,
    TargetAudienceInfo,
    ValueProposition,
    _sanitize_and_validate_overview,
)


class TestBusinessOverviewSchemaAndQuality(unittest.TestCase):
    """Test suite covering the 18 specific requirements for Issue 4."""

    def setUp(self) -> None:
        """Create standard test fixtures."""
        self.sample_form = BusinessForm(
            business_name_and_description="Aura Skincare creates botanical formulations for urban professionals.",
            product_or_service="Restorative serums and soothing facial mists",
            target_audience="Urban professionals aged 25-45 seeking clean skincare",
            audience_unknown=False,
            business_website="https://aura-skincare.example.com",
        )
        self.crawled_pages = [
            ScrapedPage(
                url="https://aura-skincare.example.com/",
                page_type=PageType.HOME,
                title="Aura Skincare | Botanical Formulations",
                content="Aura Skincare creates high performance botanical skincare for daily pollution defense.",
            ),
            ScrapedPage(
                url="https://aura-skincare.example.com/products/hydrating-serum",
                page_type=PageType.PRODUCT,
                title="Hydrating Serum | Aura Skincare",
                content="Hydrating Serum. Regular price $45, on sale for $38. Available in 30ml and 50ml bottles.",
            ),
            ScrapedPage(
                url="https://aura-skincare.example.com/collections/serums",
                page_type=PageType.PRODUCT,
                title="All Serums | Aura Skincare",
                content="Explore our complete range of plant-based treatment serums.",
            ),
            ScrapedPage(
                url="https://aura-skincare.example.com/services/skin-consultation",
                page_type=PageType.SERVICE,
                title="Virtual Skin Consultation | Aura Skincare",
                content="Book a 1-on-1 skin consultation with our licensed esthetician. $75 per session.",
            ),
        ]

    # --- Scenario 1: E-commerce individual products and collections ---
    def test_01_distinguish_individual_products_and_collections(self) -> None:
        """Verify offerings distinguish individual products from collection/category pages."""
        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="Aura Skincare offers botanical skincare lines.",
            products_and_services=[
                ProductServiceItem(
                    name="Hydrating Glow Serum",
                    offering_type=OfferingType.PRODUCT,
                    description="Individual 30ml hydrating serum.",
                    source_url="https://aura-skincare.example.com/products/hydrating-serum",
                ),
                ProductServiceItem(
                    name="Botanical Serums Collection",
                    offering_type=OfferingType.COLLECTION,
                    description="Full catalog collection of treatment serums.",
                    source_url="https://aura-skincare.example.com/collections/serums",
                ),
            ],
            target_audience=TargetAudienceInfo(
                description="Urban professionals",
                is_explicit=True,
            ),
            positioning_and_value_propositions=[
                ValueProposition(
                    statement="Clean plant-based defense",
                    is_explicit=True,
                    source_url="https://aura-skincare.example.com/",
                )
            ],
            key_findings=[],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, self.sample_form)
        self.assertEqual(len(sanitized.products_and_services), 2)
        self.assertEqual(sanitized.products_and_services[0].offering_type, OfferingType.PRODUCT)
        self.assertEqual(sanitized.products_and_services[1].offering_type, OfferingType.COLLECTION)

    # --- Scenario 2: Product variants with different shades, colors, or sizes ---
    def test_02_product_variants_supported(self) -> None:
        """Verify product variants (shades, sizes, volumes) are preserved on ProductServiceItem."""
        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="Aura Skincare offers sized serums.",
            products_and_services=[
                ProductServiceItem(
                    name="Hydrating Glow Serum",
                    offering_type=OfferingType.PRODUCT,
                    variants=["15ml travel size", "30ml standard", "50ml jumbo"],
                    description="Deep moisture barrier serum.",
                    source_url="https://aura-skincare.example.com/products/hydrating-serum",
                ),
            ],
            target_audience=TargetAudienceInfo(description="General consumers", is_explicit=False),
            positioning_and_value_propositions=[],
            key_findings=[],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, self.sample_form)
        item = sanitized.products_and_services[0]
        self.assertEqual(len(item.variants), 3)
        self.assertIn("15ml travel size", item.variants)
        self.assertIn("50ml jumbo", item.variants)

    # --- Scenario 3: Product with original and discounted prices ---
    def test_03_original_and_discounted_prices(self) -> None:
        """Verify structured pricing captures price, original_price, and discount_text."""
        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="Discounted products overview.",
            products_and_services=[
                ProductServiceItem(
                    name="Hydrating Glow Serum",
                    offering_type=OfferingType.PRODUCT,
                    description="Deep moisture barrier serum.",
                    pricing=PricingInfo(
                        price="$38.00",
                        original_price="$45.00",
                        currency="USD",
                        discount_text="Save $7.00 (15% off) with coupon GLOW15",
                        availability=PriceAvailability.AVAILABLE,
                    ),
                    source_url="https://aura-skincare.example.com/products/hydrating-serum",
                ),
            ],
            target_audience=TargetAudienceInfo(description="Consumers", is_explicit=False),
            positioning_and_value_propositions=[],
            key_findings=[],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, self.sample_form)
        pricing = sanitized.products_and_services[0].pricing
        self.assertIsNotNone(pricing)
        self.assertEqual(pricing.price, "$38.00")
        self.assertEqual(pricing.original_price, "$45.00")
        self.assertEqual(pricing.currency, "USD")
        self.assertEqual(pricing.discount_text, "Save $7.00 (15% off) with coupon GLOW15")
        self.assertEqual(pricing.availability, PriceAvailability.AVAILABLE)

    # --- Scenario 4: Product with an unavailable price ---
    def test_04_product_with_unavailable_price(self) -> None:
        """Verify products without pricing information are marked PriceAvailability.NOT_STATED."""
        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="Overview with unstated price.",
            products_and_services=[
                ProductServiceItem(
                    name="Sample Lab Elixir",
                    offering_type=OfferingType.PRODUCT,
                    description="Limited batch formulation.",
                    pricing=PricingInfo(
                        price=None,
                        availability=PriceAvailability.NOT_STATED,
                    ),
                    source_url="https://aura-skincare.example.com/products/hydrating-serum",
                ),
            ],
            target_audience=TargetAudienceInfo(description="Consumers", is_explicit=False),
            positioning_and_value_propositions=[],
            key_findings=[],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, self.sample_form)
        pricing = sanitized.products_and_services[0].pricing
        self.assertIsNotNone(pricing)
        self.assertIsNone(pricing.price)
        self.assertEqual(pricing.availability, PriceAvailability.NOT_STATED)

    # --- Scenario 5: Service business with packages or per-session pricing ---
    def test_05_service_business_with_per_session_pricing(self) -> None:
        """Verify service businesses support SERVICE_PACKAGE and billing_period='per session'."""
        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="Service provider overview.",
            products_and_services=[
                ProductServiceItem(
                    name="Personalized Skin Audit",
                    offering_type=OfferingType.SERVICE_PACKAGE,
                    description="One-on-one virtual skin evaluation.",
                    pricing=PricingInfo(
                        price="$75.00",
                        billing_period="per session",
                        availability=PriceAvailability.AVAILABLE,
                    ),
                    source_url="https://aura-skincare.example.com/services/skin-consultation",
                ),
            ],
            target_audience=TargetAudienceInfo(description="Skincare seekers", is_explicit=False),
            positioning_and_value_propositions=[],
            key_findings=[],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, self.sample_form)
        item = sanitized.products_and_services[0]
        self.assertEqual(item.offering_type, OfferingType.SERVICE_PACKAGE)
        self.assertEqual(item.pricing.billing_period, "per session")
        self.assertEqual(item.pricing.price, "$75.00")

    # --- Scenario 6: SaaS business with monthly and annual plans ---
    def test_06_saas_business_subscription_plans(self) -> None:
        """Verify SaaS subscription plans with monthly and annual billing periods."""
        saas_pages = [
            ScrapedPage(
                url="https://cloudapp.example.com/pricing",
                page_type=PageType.PRICING,
                title="Pricing Plans | CloudApp",
                content="Starter plan $29/mo or $290/year billed annually.",
            )
        ]
        saas_form = BusinessForm(
            business_name_and_description="CloudApp provides continuous uptime monitoring.",
            product_or_service="Cloud monitoring subscriptions",
            target_audience="DevOps and SRE teams",
            business_website="https://cloudapp.example.com",
        )

        overview = BusinessOverview(
            business_name="CloudApp",
            website_url="https://cloudapp.example.com",
            business_description="SaaS uptime monitoring.",
            products_and_services=[
                ProductServiceItem(
                    name="Starter Plan (Monthly)",
                    offering_type=OfferingType.SUBSCRIPTION_PLAN,
                    description="Basic uptime monitoring billed monthly.",
                    pricing=PricingInfo(
                        price="$29",
                        billing_period="monthly",
                        availability=PriceAvailability.AVAILABLE,
                    ),
                    source_url="https://cloudapp.example.com/pricing",
                ),
                ProductServiceItem(
                    name="Starter Plan (Annual)",
                    offering_type=OfferingType.SUBSCRIPTION_PLAN,
                    description="Basic uptime monitoring billed annually.",
                    pricing=PricingInfo(
                        price="$290",
                        billing_period="annual",
                        discount_text="Save 17% with annual billing",
                        availability=PriceAvailability.AVAILABLE,
                    ),
                    source_url="https://cloudapp.example.com/pricing",
                ),
            ],
            target_audience=TargetAudienceInfo(description="DevOps engineers", is_explicit=False),
            positioning_and_value_propositions=[],
            key_findings=[],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, saas_pages, saas_form)
        self.assertEqual(len(sanitized.products_and_services), 2)
        self.assertEqual(sanitized.products_and_services[0].offering_type, OfferingType.SUBSCRIPTION_PLAN)
        self.assertEqual(sanitized.products_and_services[0].pricing.billing_period, "monthly")
        self.assertEqual(sanitized.products_and_services[1].offering_type, OfferingType.SUBSCRIPTION_PLAN)
        self.assertEqual(sanitized.products_and_services[1].pricing.billing_period, "annual")

    # --- Scenario 7: Free trial or promotional bundle ---
    def test_07_bundle_and_free_trial_guarantee(self) -> None:
        """Verify trial_or_guarantee field and BUNDLE offering type are supported."""
        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="Bundle offering overview.",
            products_and_services=[
                ProductServiceItem(
                    name="Complete Barrier Defense Bundle",
                    offering_type=OfferingType.BUNDLE,
                    description="Full multi-product barrier restoration kit.",
                    pricing=PricingInfo(
                        price="$75.00",
                        original_price="$95.00",
                        discount_text="21% bundle discount",
                        trial_or_guarantee="30-day money-back guarantee",
                        availability=PriceAvailability.AVAILABLE,
                    ),
                    source_url="https://aura-skincare.example.com/collections/serums",
                ),
            ],
            target_audience=TargetAudienceInfo(description="Consumers", is_explicit=False),
            positioning_and_value_propositions=[],
            key_findings=[],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, self.sample_form)
        item = sanitized.products_and_services[0]
        self.assertEqual(item.offering_type, OfferingType.BUNDLE)
        self.assertEqual(item.pricing.trial_or_guarantee, "30-day money-back guarantee")

    # --- Scenario 8: Website that does not disclose pricing ---
    def test_08_undisclosed_or_quote_based_pricing(self) -> None:
        """Verify quote-based services where price is undisclosed."""
        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="Enterprise consulting overview.",
            products_and_services=[
                ProductServiceItem(
                    name="Custom Corporate Wellness Workshop",
                    offering_type=OfferingType.SERVICE,
                    description="Tailored seminar for enterprise organizations.",
                    pricing=PricingInfo(
                        price=None,
                        availability=PriceAvailability.QUOTE_BASED,
                    ),
                    source_url="https://aura-skincare.example.com/services/skin-consultation",
                ),
            ],
            target_audience=TargetAudienceInfo(description="Corporate teams", is_explicit=False),
            positioning_and_value_propositions=[],
            key_findings=[],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, self.sample_form)
        item = sanitized.products_and_services[0]
        self.assertEqual(item.pricing.availability, PriceAvailability.QUOTE_BASED)
        self.assertIsNone(item.pricing.price)

    # --- Scenario 9: Target audience explicitly stated on website ---
    def test_09_target_audience_explicitly_stated(self) -> None:
        """Verify target audience marked is_explicit=True when explicitly stated on website."""
        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="Targeted skincare brand.",
            products_and_services=[],
            target_audience=TargetAudienceInfo(
                description="Urban professionals aged 25-45 exposed to city environmental stressors",
                is_explicit=True,
                source_url="https://aura-skincare.example.com/",
            ),
            positioning_and_value_propositions=[],
            key_findings=[],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, self.sample_form)
        self.assertTrue(sanitized.target_audience.is_explicit)
        self.assertIn("Urban professionals", sanitized.target_audience.description)

    # --- Scenario 10: Target audience not explicitly stated ---
    def test_10_target_audience_not_explicitly_stated(self) -> None:
        """Verify fallback behavior when audience is not explicit on the site and user input is unknown."""
        unknown_form = BusinessForm(
            business_name_and_description="Mystery Brand sells generic goods.",
            product_or_service="Assorted tools",
            audience_unknown=True,
            business_website="https://aura-skincare.example.com",
        )
        overview = BusinessOverview(
            business_name="Mystery Brand",
            website_url="https://aura-skincare.example.com",
            business_description="General store.",
            products_and_services=[],
            target_audience=TargetAudienceInfo(
                description="Inferred general public",
                is_explicit=False,
            ),
            positioning_and_value_propositions=[],
            key_findings=[],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, unknown_form)
        self.assertFalse(sanitized.target_audience.is_explicit)

    # --- Scenario 11: Unsupported product features or claims handled appropriately ---
    def test_11_unsupported_features_handled_properly(self) -> None:
        """Verify that items without valid sources have their hallucinated source URLs stripped while retaining safe text."""
        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="Overview with an unverified claim.",
            products_and_services=[
                ProductServiceItem(
                    name="Miracle Cure Cream",
                    description="Clinically eliminates 100% of fine lines overnight without irritation.",
                    source_url="https://aura-skincare.example.com/unverified-clinical-claim",
                )
            ],
            target_audience=TargetAudienceInfo(description="Consumers", is_explicit=False),
            positioning_and_value_propositions=[],
            key_findings=[],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, self.sample_form)
        # Hallucinated source URL is stripped
        self.assertIsNone(sanitized.products_and_services[0].source_url)
        self.assertEqual(sanitized.products_and_services[0].source_urls, [])
        # The hallucinated URL does not enter sources_used
        self.assertNotIn("https://aura-skincare.example.com/unverified-clinical-claim", sanitized.sources_used)

    # --- Scenario 12: Hallucinated source URLs rejected across all models ---
    def test_12_hallucinated_source_urls_rejected(self) -> None:
        """Verify hallucinated URLs on products, VPs, and findings are stripped."""
        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="Overview with fake citations.",
            products_and_services=[
                ProductServiceItem(
                    name="Hydrating Serum",
                    description="Botanical formulation serum.",
                    source_url="https://hallucinated-store.com/fake-product",
                    source_urls=["https://hallucinated-store.com/fake-product"],
                )
            ],
            target_audience=TargetAudienceInfo(description="Consumers", is_explicit=False),
            positioning_and_value_propositions=[
                ValueProposition(
                    statement="Best botanical skincare",
                    is_explicit=True,
                    source_url="https://hallucinated-store.com/fake-vp",
                    source_urls=["https://hallucinated-store.com/fake-vp"],
                )
            ],
            key_findings=[
                Finding(
                    statement="Award winner in 2026 named best serum.",
                    finding_type=FindingType.STRENGTH,
                    source_url="https://hallucinated-store.com/fake-awards",
                    source_urls=["https://hallucinated-store.com/fake-awards"],
                )
            ],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, self.sample_form)
        self.assertIsNone(sanitized.products_and_services[0].source_url)
        self.assertEqual(sanitized.products_and_services[0].source_urls, [])
        self.assertIsNone(sanitized.positioning_and_value_propositions[0].source_url)
        self.assertEqual(sanitized.positioning_and_value_propositions[0].source_urls, [])
        self.assertEqual(len(sanitized.key_findings), 0)
        self.assertEqual(sanitized.sources_used, [])

    # --- Scenario 13: Valid source URLs with harmless formatting differences resolved ---
    def test_13_formatting_differences_resolved(self) -> None:
        """Verify trailing slashes, www prefix, schemes, and queries are resolved to crawled pages."""
        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="Overview with URL variations.",
            products_and_services=[
                ProductServiceItem(
                    name="Hydrating Glow Serum",
                    description="Botanical formulation serum.",
                    source_url="http://www.aura-skincare.example.com/products/hydrating-serum?variant=30ml",
                    source_urls=["http://www.aura-skincare.example.com/products/hydrating-serum?variant=30ml"],
                )
            ],
            target_audience=TargetAudienceInfo(description="Consumers", is_explicit=False),
            positioning_and_value_propositions=[
                ValueProposition(
                    statement="Botanical formulations",
                    is_explicit=True,
                    source_url="https://aura-skincare.example.com",  # Missing trailing slash compared to homepage
                )
            ],
            key_findings=[],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, self.sample_form)
        self.assertEqual(
            sanitized.products_and_services[0].source_url,
            "https://aura-skincare.example.com/products/hydrating-serum",
        )
        self.assertEqual(
            sanitized.products_and_services[0].source_urls,
            ["https://aura-skincare.example.com/products/hydrating-serum"],
        )
        self.assertEqual(
            sanitized.positioning_and_value_propositions[0].source_url,
            "https://aura-skincare.example.com/",
        )
        self.assertIn("https://aura-skincare.example.com/products/hydrating-serum", sanitized.sources_used)
        self.assertIn("https://aura-skincare.example.com/", sanitized.sources_used)

    # --- Scenario 14: Findings supported by multiple pages ---
    def test_14_findings_supported_by_multiple_pages(self) -> None:
        """Verify source_urls list retains and verifies all matching crawl URLs."""
        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="Multi-source finding overview.",
            products_and_services=[],
            target_audience=TargetAudienceInfo(description="Consumers", is_explicit=False),
            positioning_and_value_propositions=[],
            key_findings=[
                Finding(
                    statement="All serums are vegan and 100% cruelty-free botanical skincare.",
                    finding_type=FindingType.POSITIONING,
                    source_url="https://aura-skincare.example.com/",
                    source_urls=[
                        "https://aura-skincare.example.com/",
                        "https://aura-skincare.example.com/collections/serums",
                    ],
                )
            ],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, self.sample_form)
        self.assertEqual(len(sanitized.key_findings), 1)
        finding = sanitized.key_findings[0]
        self.assertEqual(len(finding.source_urls), 2)
        self.assertIn("https://aura-skincare.example.com/", finding.source_urls)
        self.assertIn("https://aura-skincare.example.com/collections/serums", finding.source_urls)
        self.assertIn("https://aura-skincare.example.com/", sanitized.sources_used)
        self.assertIn("https://aura-skincare.example.com/collections/serums", sanitized.sources_used)

    # --- Scenario 15: Conflicting user-provided and website-derived information ---
    def test_15_conflicting_user_and_website_information(self) -> None:
        """Verify audience conflicts are detected, flagged in has_conflict, and recorded in conflicts list."""
        b2b_form = BusinessForm(
            business_name_and_description="Aura Skincare B2B wholesale.",
            product_or_service="Wholesale bulk cosmetic ingredients",
            target_audience="B2B cosmetic manufacturers and wholesale retailers",
            audience_unknown=False,
            business_website="https://aura-skincare.example.com",
        )

        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="B2C direct skincare brand.",
            products_and_services=[],
            target_audience=TargetAudienceInfo(
                description="Everyday direct B2C retail consumers",
                is_explicit=True,
                source_url="https://aura-skincare.example.com/",
            ),
            positioning_and_value_propositions=[],
            key_findings=[],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, b2b_form)
        self.assertTrue(sanitized.target_audience.has_conflict)
        self.assertEqual(
            sanitized.target_audience.user_stated_audience,
            "B2B cosmetic manufacturers and wholesale retailers",
        )
        self.assertGreater(len(sanitized.conflicts), 0)
        self.assertTrue(any("Audience discrepancy" in c for c in sanitized.conflicts))

    # --- Scenario 16: Missing optional fields handled without error ---
    def test_16_missing_optional_fields_handled_cleanly(self) -> None:
        """Verify minimal instantiation with missing optional fields parses and sanitizes cleanly."""
        minimal_overview = BusinessOverview(
            website_url="https://aura-skincare.example.com",
            business_description="A concise summary of the brand.",
            products_and_services=[
                ProductServiceItem(
                    name="Minimal Product",
                    description="Standard product description.",
                    # No pricing, no variants, no source_urls
                )
            ],
            target_audience=TargetAudienceInfo(description="General audience", is_explicit=False),
            positioning_and_value_propositions=[
                ValueProposition(statement="High purity ingredients", is_explicit=False)
            ],
            key_findings=[],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(minimal_overview, self.crawled_pages, self.sample_form)
        self.assertEqual(sanitized.products_and_services[0].offering_type, OfferingType.PRODUCT)
        self.assertIsNone(sanitized.products_and_services[0].pricing)
        self.assertEqual(sanitized.products_and_services[0].variants, [])
        self.assertEqual(sanitized.products_and_services[0].source_urls, [])
        self.assertEqual(sanitized.conflicts, [])
        self.assertEqual(sanitized.information_gaps, [])

    # --- Scenario 17: Existing valid findings surviving sanitization ---
    def test_17_valid_findings_survive_sanitization(self) -> None:
        """Verify legitimate findings with exact quotes and verified URLs are preserved."""
        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="Overview with valid finding.",
            products_and_services=[],
            target_audience=TargetAudienceInfo(description="Urban professionals", is_explicit=False),
            positioning_and_value_propositions=[],
            key_findings=[
                Finding(
                    statement="Deep moisture barrier serum offers daily hydration.",
                    finding_type=FindingType.PRODUCT_SERVICE,
                    source_url="https://aura-skincare.example.com/products/hydrating-serum",
                )
            ],
            sources_used=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, self.sample_form)
        self.assertEqual(len(sanitized.key_findings), 1)
        self.assertEqual(sanitized.key_findings[0].statement, "Deep moisture barrier serum offers daily hydration.")
        self.assertEqual(
            sanitized.key_findings[0].source_url,
            "https://aura-skincare.example.com/products/hydrating-serum",
        )
        self.assertIn("https://aura-skincare.example.com/products/hydrating-serum", sanitized.sources_used)

    # --- Scenario 18: Final API response conforming to updated schema ---
    def test_18_final_api_response_schema_conformance(self) -> None:
        """Verify BusinessOverviewResponse serializes all Issue 4 enhancements cleanly."""
        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="Aura Skincare produces pollution defense cosmetics.",
            products_and_services=[
                ProductServiceItem(
                    name="Hydrating Glow Serum",
                    description="Daily pollution defense facial serum.",
                    offering_type=OfferingType.PRODUCT,
                    variants=["30ml", "50ml"],
                    pricing=PricingInfo(
                        price="$38.00",
                        original_price="$45.00",
                        discount_text="Save $7.00",
                        availability=PriceAvailability.AVAILABLE,
                    ),
                    source_url="https://aura-skincare.example.com/products/hydrating-serum",
                    source_urls=["https://aura-skincare.example.com/products/hydrating-serum"],
                ),
                ProductServiceItem(
                    name="Virtual Skin Consultation",
                    description="1-on-1 virtual evaluation with a certified esthetician.",
                    offering_type=OfferingType.SERVICE,
                    pricing=PricingInfo(
                        price="$75.00",
                        billing_period="per session",
                        availability=PriceAvailability.AVAILABLE,
                    ),
                    source_url="https://aura-skincare.example.com/services/skin-consultation",
                ),
            ],
            target_audience=TargetAudienceInfo(
                description="Urban professionals aged 25-45",
                is_explicit=True,
                user_stated_audience="Urban professionals aged 25-45 seeking clean skincare",
                has_conflict=False,
            ),
            positioning_and_value_propositions=[
                ValueProposition(
                    statement="Botanical pollution defense",
                    is_explicit=True,
                    source_url="https://aura-skincare.example.com/",
                    source_urls=["https://aura-skincare.example.com/"],
                )
            ],
            key_findings=[
                Finding(
                    statement="Plant-based formulations defending against pollution.",
                    finding_type=FindingType.POSITIONING,
                    source_url="https://aura-skincare.example.com/",
                    source_urls=["https://aura-skincare.example.com/"],
                )
            ],
            sources_used=[
                "https://aura-skincare.example.com/",
                "https://aura-skincare.example.com/products/hydrating-serum",
                "https://aura-skincare.example.com/services/skin-consultation",
            ],
            information_gaps=[
                InformationGap(
                    category="Certifications",
                    observation="No explicit organic certification logos verified on crawled pages.",
                )
            ],
            conflicts=[],
        )

        response = BusinessOverviewResponse(
            status="success",
            business_overview=overview,
            website_url="https://aura-skincare.example.com",
            execution_time_seconds=3.42,
        )

        response_dict = response.model_dump()
        self.assertEqual(response_dict["status"], "success")
        self.assertEqual(response_dict["website_url"], "https://aura-skincare.example.com")
        self.assertEqual(response_dict["execution_time_seconds"], 3.42)

        bo = response_dict["business_overview"]
        self.assertEqual(len(bo["products_and_services"]), 2)
        self.assertEqual(bo["products_and_services"][0]["offering_type"], "product")
        self.assertEqual(bo["products_and_services"][0]["variants"], ["30ml", "50ml"])
        self.assertEqual(bo["products_and_services"][0]["pricing"]["price"], "$38.00")
        self.assertEqual(bo["products_and_services"][0]["pricing"]["original_price"], "$45.00")
        self.assertEqual(bo["products_and_services"][0]["pricing"]["discount_text"], "Save $7.00")
        self.assertEqual(bo["products_and_services"][0]["pricing"]["availability"], "available")
        self.assertEqual(bo["products_and_services"][1]["offering_type"], "service")
        self.assertEqual(bo["products_and_services"][1]["pricing"]["billing_period"], "per session")

        self.assertTrue(bo["target_audience"]["is_explicit"])
        self.assertFalse(bo["target_audience"]["has_conflict"])
        self.assertIn("conflicts", bo)
        self.assertEqual(bo["conflicts"], [])

        # Verify JSON serialization works seamlessly
        json_output = response.model_dump_json()
        self.assertIn('"status":"success"', json_output)
        self.assertIn('"offering_type":"product"', json_output)
        self.assertIn('"billing_period":"per session"', json_output)

    # --- Scenario 19: System prompt contains customer-facing language rules ---
    def test_19_customer_facing_language_rules_in_system_prompt(self) -> None:
        """Verify BUSINESS_OVERVIEW_SYSTEM_PROMPT includes CUSTOMER-FACING LANGUAGE RULES."""
        from backend.app.agent.prompts.business_overview import BUSINESS_OVERVIEW_SYSTEM_PROMPT

        self.assertIn("CUSTOMER-FACING LANGUAGE RULES", BUSINESS_OVERVIEW_SYSTEM_PROMPT)
        self.assertIn("Never mention internal data collection, crawling, scraping", BUSINESS_OVERVIEW_SYSTEM_PROMPT)
        self.assertIn("crawled page", BUSINESS_OVERVIEW_SYSTEM_PROMPT)
        self.assertIn("supplied pages", BUSINESS_OVERVIEW_SYSTEM_PROMPT)
        self.assertIn("page excerpt", BUSINESS_OVERVIEW_SYSTEM_PROMPT)
        self.assertIn("the content we analyzed", BUSINESS_OVERVIEW_SYSTEM_PROMPT)
        self.assertIn("Scope the statement accurately", BUSINESS_OVERVIEW_SYSTEM_PROMPT)
        self.assertIn("Do not turn uncertainty into a definitive claim", BUSINESS_OVERVIEW_SYSTEM_PROMPT)

    # --- Scenario 23: Preserves meaning, uncertainty, and structured metadata ---
    def test_23_preserves_meaning_uncertainty_and_structured_metadata(self) -> None:
        """Verify source URLs, pricing values, variants, and honest uncertainty remain intact."""
        overview = BusinessOverview(
            business_name="Aura Skincare",
            website_url="https://aura-skincare.example.com",
            business_description="The supplied pages indicate high quality.",
            products_and_services=[
                ProductServiceItem(
                    name="CRAZE Blush pH-lease",
                    offering_type=OfferingType.PRODUCT,
                    description="The crawled page reports pH-adaptive blush.",
                    variants=["Cheeky Pink", "Peach Glow"],
                    pricing=PricingInfo(
                        price="₹269",
                        original_price="₹399",
                        currency="INR",
                        discount_text="Coupon: ₹175",
                        availability=PriceAvailability.AVAILABLE,
                    ),
                    source_url="https://aura-skincare.example.com/products/hydrating-serum",
                    source_urls=["https://aura-skincare.example.com/products/hydrating-serum"],
                ),
            ],
            target_audience=TargetAudienceInfo(
                description="Current audience could not be verified on reviewed pages.",
                is_explicit=False,
                user_stated_audience="Urban professionals aged 25-45 seeking clean skincare",
            ),
            positioning_and_value_propositions=[],
            information_gaps=[
                InformationGap(
                    category="Pricing & Plans",
                    observation="Current prices for these concealers are not listed on the reviewed product pages.",
                )
            ],
            key_findings=[],
            sources_used=[],
            conflicts=[],
        )

        sanitized = _sanitize_and_validate_overview(overview, self.crawled_pages, self.sample_form)

        # Source URLs intact
        self.assertEqual(sanitized.products_and_services[0].source_url, "https://aura-skincare.example.com/products/hydrating-serum")
        self.assertEqual(sanitized.sources_used, ["https://aura-skincare.example.com/products/hydrating-serum"])

        # Structured pricing intact
        pricing = sanitized.products_and_services[0].pricing
        self.assertIsNotNone(pricing)
        self.assertEqual(pricing.price, "₹269")
        self.assertEqual(pricing.original_price, "₹399")
        self.assertEqual(pricing.currency, "INR")
        self.assertEqual(pricing.discount_text, "Coupon: ₹175")
        self.assertEqual(pricing.availability, PriceAvailability.AVAILABLE)

        # Product variants intact
        self.assertEqual(sanitized.products_and_services[0].variants, ["Cheeky Pink", "Peach Glow"])

        # User-stated audience intact
        self.assertEqual(sanitized.target_audience.user_stated_audience, "Urban professionals aged 25-45 seeking clean skincare")

        # Uncertainty preserved (not turned into site-wide claim)
        self.assertIn("not listed on the reviewed product pages", sanitized.information_gaps[0].observation)



if __name__ == "__main__":
    unittest.main()
