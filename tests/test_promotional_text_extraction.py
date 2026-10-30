"""Unit tests for Phase A: Improved Homepage Promotional Text Extraction.

Verifies:
1. A promotional announcement inside a <header> is preserved.
2. A promotional message inside an <aside> is preserved when it contains meaningful marketing content.
3. Ordinary navigation menus (<nav>, [role='navigation'], .nav-menu, link lists) are still excluded or filtered.
4. Hero headlines, subheadings, and descriptions remain in the extracted text.
5. Discount codes, coupon values, free shipping offers, and promotional CTAs are preserved.
6. Repeated promotional text (cloned slides, ticker/marquee repeats, mobile/desktop duplicates) is not unnecessarily duplicated.
7. Existing internal-page text extraction continues to work cleanly.
8. Scripts, styles, noscript, svg, iframes, cookie notices, and noise forms remain excluded.
9. Empty, whitespace-only, or tag-only HTML is handled safely (returning empty string).
10. ScrapedPage conversion retains title and full promotional content without truncation.
"""

from __future__ import annotations

import unittest
from bs4 import BeautifulSoup

# Ensure canonical package initialization order
import backend.app.main  # noqa: F401

from backend.app.schemas.enums import PageType
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.homepage_service import HomepageFetchResult
from backend.app.services.website_content_crawler_service import (
    _clean_html_fragment,
    _extract_best_content,
    _extract_text_from_html,
)
from backend.app.agent.nodes.url_discovery_node import _homepage_html_to_scraped_page


class TestPromotionalTextExtraction(unittest.TestCase):
    """Synthetic test suite for promotional and marketing text extraction."""

    # -------------------------------------------------------------------------
    # Case 1: Promotional announcement inside <header> is preserved
    # -------------------------------------------------------------------------
    def test_01_promotional_announcement_in_header_preserved(self) -> None:
        """Announcement bar inside <header> survives extraction."""
        html = """
        <!DOCTYPE html>
        <html>
        <head><title>Aura Skincare</title></head>
        <body>
            <header class="site-header">
                <div class="announcement-bar">
                    <p>Free shipping on all orders over $50 | Use code WELCOME20 for 20% off</p>
                </div>
                <nav class="main-navigation">
                    <ul>
                        <li><a href="/shop">Shop</a></li>
                        <li><a href="/about">About Us</a></li>
                    </ul>
                </nav>
            </header>
            <main>
                <h1>Clean Botanical Beauty</h1>
                <p>Nourish your skin with organic cold-pressed serums and oils.</p>
            </main>
        </body>
        </html>
        """
        extracted = _extract_text_from_html(html)

        self.assertIn("Free shipping on all orders over $50", extracted)
        self.assertIn("WELCOME20", extracted)
        self.assertIn("20% off", extracted)
        self.assertIn("Clean Botanical Beauty", extracted)
        self.assertIn("Nourish your skin", extracted)
        # Navigation links must not leak into content
        self.assertNotIn("Shop", extracted)
        self.assertNotIn("About Us", extracted)

    # -------------------------------------------------------------------------
    # Case 2: Promotional message inside <aside> is preserved
    # -------------------------------------------------------------------------
    def test_02_promotional_aside_preserved(self) -> None:
        """Meaningful marketing content inside an <aside> is preserved."""
        html = """
        <aside class="promo-banner">
            <div class="banner-inner">
                <strong>Limited Time Flash Sale:</strong>
                <span>Save up to 40% on all anti-aging moisturizers with code FLASH40.</span>
                <span>30-day money-back guarantee on every order!</span>
            </div>
        </aside>
        <section class="products">
            <h2>Featured Moisturizers</h2>
            <p>Hydrating Ceramide Cream - Price: $38.00</p>
        </section>
        """
        extracted = _extract_text_from_html(html)

        self.assertIn("Limited Time Flash Sale:", extracted)
        self.assertIn("Save up to 40%", extracted)
        self.assertIn("FLASH40", extracted)
        self.assertIn("30-day money-back guarantee", extracted)
        self.assertIn("Hydrating Ceramide Cream", extracted)
        self.assertIn("Price: $38.00", extracted)

    # -------------------------------------------------------------------------
    # Case 3: Ordinary navigation menus are excluded
    # -------------------------------------------------------------------------
    def test_03_ordinary_navigation_menus_excluded(self) -> None:
        """Pure navigation menus, links lists, and role='navigation' are stripped."""
        html = """
        <header>
            <div class="top-announcement">
                <span>Special Spring Deal: Buy 2 get 1 free!</span>
            </div>
            <div class="navbar" role="navigation">
                <a href="/catalog">All Products</a>
                <a href="/skincare">Skincare</a>
                <a href="/haircare">Haircare</a>
                <a href="/cart">Shopping Bag (0)</a>
            </div>
            <div class="header-menu">
                <ul>
                    <li><a href="/login">Sign In</a></li>
                    <li><a href="/register">Create Account</a></li>
                </ul>
            </div>
        </header>
        <main>
            <p>Handcrafted artisan soaps made with lavender and shea butter.</p>
        </main>
        """
        extracted = _extract_text_from_html(html)

        self.assertIn("Special Spring Deal: Buy 2 get 1 free!", extracted)
        self.assertIn("Handcrafted artisan soaps", extracted)

        # Navigation elements must be excluded
        self.assertNotIn("All Products", extracted)
        self.assertNotIn("Skincare", extracted)
        self.assertNotIn("Haircare", extracted)
        self.assertNotIn("Shopping Bag", extracted)
        self.assertNotIn("Sign In", extracted)
        self.assertNotIn("Create Account", extracted)

    # -------------------------------------------------------------------------
    # Case 4: Hero headlines and descriptions remain in extracted text
    # -------------------------------------------------------------------------
    def test_04_hero_section_headlines_and_descriptions_preserved(self) -> None:
        """Hero sections wrapped inside <header> or <section> preserve headlines and CTAs."""
        html = """
        <header class="hero-header">
            <div class="hero-container">
                <h1 class="hero-title">Automate Competitor Tracking in Real Time</h1>
                <p class="hero-subtitle">
                    Antigravity tracks pricing moves, ad promotions, and catalogue changes across thousands of competitor storefronts.
                </p>
                <div class="hero-cta">
                    <a href="/trial" class="btn btn-primary">Start Your 14-Day Free Trial</a>
                    <span class="cta-note">No credit card required. Cancel anytime.</span>
                </div>
            </div>
        </header>
        """
        extracted = _extract_text_from_html(html)

        self.assertIn("Automate Competitor Tracking in Real Time", extracted)
        self.assertIn("Antigravity tracks pricing moves", extracted)
        self.assertIn("Start Your 14-Day Free Trial", extracted)
        self.assertIn("No credit card required. Cancel anytime.", extracted)

    # -------------------------------------------------------------------------
    # Case 5: Discount codes, shipping offers, and CTAs preserved
    # -------------------------------------------------------------------------
    def test_05_discount_codes_shipping_offers_and_ctas_preserved(self) -> None:
        """Discount codes, shipping guarantees, and trust badges survive extraction."""
        html = """
        <div class="top-bar">
            <span class="delivery-offer">Enjoy free express delivery on orders over $75</span>
            <span class="promo-code">Use discount code EXTRA15 at checkout</span>
        </div>
        <div class="trust-highlights">
            <span>100% cruelty-free & certified organic formulas</span>
            <span>Dermatologist-tested with 30-day money-back guarantee</span>
        </div>
        <div class="offer-cta">
            <a href="/shop-sale">Shop the Sale and Save Now</a>
        </div>
        """
        extracted = _extract_text_from_html(html)

        self.assertIn("free express delivery on orders over $75", extracted)
        self.assertIn("EXTRA15", extracted)
        self.assertIn("100% cruelty-free & certified organic formulas", extracted)
        self.assertIn("Dermatologist-tested with 30-day money-back guarantee", extracted)
        self.assertIn("Shop the Sale and Save Now", extracted)

    # -------------------------------------------------------------------------
    # Case 6: Repeated promotional text is not unnecessarily duplicated
    # -------------------------------------------------------------------------
    def test_06_repeated_promotional_text_not_duplicated(self) -> None:
        """Mobile/desktop clones and carousel slide duplicates are collapsed."""
        html = """
        <header>
            <div class="announcement-bar">
                <div class="announcement-text desktop">Free nationwide shipping on orders over $50</div>
                <div class="announcement-text mobile">Free nationwide shipping on orders over $50</div>
            </div>
            <div class="announcement-slider">
                <div class="slide swiper-slide-active">Use code SAVE10 for $10 off your first box</div>
                <div class="slide swiper-slide-duplicate">Use code SAVE10 for $10 off your first box</div>
            </div>
        </header>
        <main>
            <p>Curated organic tea boxes delivered to your doorstep monthly.</p>
        </main>
        """
        extracted = _extract_text_from_html(html)

        self.assertEqual(extracted.count("Free nationwide shipping on orders over $50"), 1)
        self.assertEqual(extracted.count("Use code SAVE10 for $10 off your first box"), 1)
        self.assertIn("Curated organic tea boxes", extracted)

    # -------------------------------------------------------------------------
    # Case 7: Existing internal-page text extraction continues to work
    # -------------------------------------------------------------------------
    def test_07_internal_page_extraction_works_cleanly(self) -> None:
        """Internal page content (pricing tiers, descriptions, forms) extracts accurately."""
        html = """
        <div class="pricing-container">
            <h1>Pricing & Subscription Plans</h1>
            <div class="tier">
                <h2>Starter Plan</h2>
                <p class="price">$29 / month</p>
                <p class="features">Up to 5 competitor websites tracked, daily email digests.</p>
            </div>
            <div class="tier">
                <h2>Professional Plan</h2>
                <p class="price">$89 / month</p>
                <p class="features">Unlimited competitor websites, instant price alert webhooks.</p>
            </div>
            <form action="/subscribe" method="POST">
                <input type="hidden" name="csrf" value="secret_token" />
                <button type="submit">Choose Professional</button>
            </form>
        </div>
        """
        extracted = _extract_text_from_html(html)

        self.assertIn("Pricing & Subscription Plans", extracted)
        self.assertIn("Starter Plan", extracted)
        self.assertIn("$29 / month", extracted)
        self.assertIn("Professional Plan", extracted)
        self.assertIn("$89 / month", extracted)
        self.assertIn("instant price alert webhooks", extracted)
        self.assertNotIn("secret_token", extracted)

        # Also verify fallback extraction in _extract_best_content
        item = {
            "markdown": "",  # Markdown empty -> must fall back to HTML
            "text": "",
            "html": html,
        }
        best_content = _extract_best_content(item)
        self.assertIn("Professional Plan", best_content)
        self.assertIn("$89 / month", best_content)

    # -------------------------------------------------------------------------
    # Case 8: Scripts, styles, and irrelevant boilerplate remain excluded
    # -------------------------------------------------------------------------
    def test_08_scripts_styles_and_boilerplate_excluded(self) -> None:
        """Technical noise tags, cookie banners, search and newsletter forms are stripped."""
        html = """
        <html>
        <head>
            <style>body { background: #fff; } .banner { color: red; }</style>
            <script>console.log("analytics tracking code 12345");</script>
            <noscript>Please enable JavaScript to view this site.</noscript>
        </head>
        <body>
            <div id="cookie-notice" class="cookie-banner">
                <p>We use cookies to enhance your experience. Accept all cookies.</p>
            </div>
            <form role="search" action="/search">
                <input type="text" placeholder="Search products..." />
            </form>
            <form class="newsletter-signup" action="/subscribe">
                <input type="email" placeholder="Enter your email" />
            </form>
            <svg><path d="M10 10 H 90 V 90 H 10 Z" /></svg>
            <aside class="sidebar-archive">
                <h4>Blog Archives</h4>
                <ul><li>January 2024</li><li>December 2023</li></ul>
            </aside>
            <footer>
                <p>© 2024 Acme Skincare Inc. All rights reserved.</p>
            </footer>
            <main>
                <h1>Organic Rosewater Mist</h1>
                <p>Pure Bulgarian rose hydrosol refreshes and tones sensitive skin.</p>
            </main>
        </body>
        </html>
        """
        extracted = _extract_text_from_html(html)

        self.assertIn("Organic Rosewater Mist", extracted)
        self.assertIn("Pure Bulgarian rose hydrosol", extracted)

        # Noise elements must be absent
        self.assertNotIn("analytics tracking code", extracted)
        self.assertNotIn("Please enable JavaScript", extracted)
        self.assertNotIn("Accept all cookies", extracted)
        self.assertNotIn("Search products", extracted)
        self.assertNotIn("Enter your email", extracted)
        self.assertNotIn("Blog Archives", extracted)
        self.assertNotIn("All rights reserved", extracted)

    # -------------------------------------------------------------------------
    # Case 9: Empty or unusable HTML handled safely
    # -------------------------------------------------------------------------
    def test_09_empty_or_unusable_html_handled_safely(self) -> None:
        """Empty, whitespace-only, and tag-only HTML strings safely return empty string."""
        self.assertEqual(_extract_text_from_html(""), "")
        self.assertEqual(_extract_text_from_html("   \n\t  "), "")
        self.assertEqual(_extract_text_from_html("<html><body></body></html>"), "")
        self.assertEqual(_extract_text_from_html("<div><span>   </span></div>"), "")

        # Non-HTML / None-safe check
        self.assertEqual(_extract_text_from_html(None), "")  # type: ignore

    # -------------------------------------------------------------------------
    # Case 10: ScrapedPage conversion retains title and promotional content
    # -------------------------------------------------------------------------
    def test_10_homepage_scraped_page_conversion_preserves_promotional_content_and_title(self) -> None:
        """_homepage_html_to_scraped_page captures extracted title and promotional offers."""
        html = """
        <!DOCTYPE html>
        <html>
        <head><title>Glow Naturals - Organic Skincare & Wellness</title></head>
        <body>
            <header>
                <div class="announcement-bar">
                    <p>Free standard shipping on orders over $40! Use code GLOW10 for 10% off.</p>
                </div>
            </header>
            <main>
                <h1>Welcome to Glow Naturals</h1>
                <p>Plant-based formulations created for sensitive skin.</p>
            </main>
        </body>
        </html>
        """
        fetch_result = HomepageFetchResult(
            requested_url="https://glownaturals.com",
            final_url="https://glownaturals.com/",
            html=html,
            status="success",
            http_status_code=200,
            error_message=None,
            execution_time_seconds=0.45,
            actor_run_id="run-test-123",
        )

        page = _homepage_html_to_scraped_page(fetch_result)
        self.assertIsNotNone(page)
        self.assertIsInstance(page, ScrapedPage)
        self.assertEqual(page.url, "https://glownaturals.com/")
        self.assertEqual(page.title, "Glow Naturals - Organic Skincare & Wellness")
        self.assertEqual(page.page_type, PageType.HOME)
        self.assertIn("Free standard shipping on orders over $40", page.content)
        self.assertIn("GLOW10", page.content)
        self.assertIn("Welcome to Glow Naturals", page.content)


if __name__ == "__main__":
    unittest.main()

