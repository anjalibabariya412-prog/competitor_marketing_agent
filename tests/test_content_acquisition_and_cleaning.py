"""Unit tests for website content acquisition, cleaning, and metadata preservation.

Covers offline validation of:
- Fix 1: Protecting useful content (prices, discounts, variants) inside forms.
- Fix 2: Safe, bounded cookie-banner removal without deleting surrounding content.
- Fix 3: Conservative duplicate-content cleaning protecting prices and headings.
- Fix 4: Reducing false positives in blocked-page detection.
- Fix 5: Quality-aware content representation fallbacks.
- Fix 6: Preserving honest HTTP status metadata (no fabricated 200s or 403s).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch
from bs4 import BeautifulSoup

# Ensure package modules initialize in canonical order to avoid circular import issues
import backend.app.main  # noqa: F401

from backend.app.schemas.enums import PageType
from backend.app.services.homepage_service import _is_blocked_html
from backend.app.services.website_content_crawler_service import (
    _clean_html_fragment,
    _clean_page_content,
    _deduplicate_paragraphs_conservatively,
    _extract_best_content,
    _extract_text_from_html,
    _is_blocked_content,
    crawl_selected_pages,
)


class TestContentCleaningAndAcquisition(unittest.IsolatedAsyncioTestCase):
    """Offline test suite for website content acquisition and cleaning fixes."""

    # -------------------------------------------------------------------------
    # Fix 1: Protect Useful Content During HTML Cleaning
    # -------------------------------------------------------------------------

    def test_form_cleaning_preserves_product_details_and_prices(self):
        """Visible product details, prices, coupon info, and variants inside forms survive."""
        html = """
        <div class="product-page">
            <form action="/cart/add" method="POST" id="add-to-cart-form">
                <input type="hidden" name="product_id" value="98765" />
                <input type="hidden" name="token" value="csrf_secret_abc" />
                <h1 class="title">Hydrating Lip Serum</h1>
                <div class="pricing">
                    <span class="regular-price">Regular: $24.00</span>
                    <span class="sale-price">Sale: $18.00</span>
                    <span class="coupon-tag">Use code SAVE10 for extra $2 off</span>
                </div>
                <div class="variants">
                    <label for="shade">Select Shade:</label>
                    <select id="shade" name="shade">
                        <option value="1">Berry Tint ($18.00)</option>
                        <option value="2">Peach Glow ($18.00)</option>
                    </select>
                </div>
                <p class="features">Enriched with Vitamin E and Jojoba Seed Oil.</p>
                <input type="submit" value="Add to Cart" />
            </form>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        _clean_html_fragment(soup)
        text = soup.get_text(separator="\n")

        # Useful commercial details must survive
        self.assertIn("Hydrating Lip Serum", text)
        self.assertIn("$24.00", text)
        self.assertIn("$18.00", text)
        self.assertIn("SAVE10", text)
        self.assertIn("Berry Tint ($18.00)", text)
        self.assertIn("Peach Glow ($18.00)", text)
        self.assertIn("Enriched with Vitamin E", text)

        # Unnecessary hidden inputs and controls must be removed
        self.assertNotIn("98765", text)
        self.assertNotIn("csrf_secret_abc", text)

    def test_form_cleaning_discards_noise_forms(self):
        """Irrelevant search and newsletter forms are discarded while content forms survive."""
        html = """
        <div>
            <form role="search" action="/search">
                <input type="text" name="q" placeholder="Search catalog..." />
                <button type="submit">Search</button>
            </form>
            <form class="newsletter-signup" action="/newsletter">
                <input type="email" name="email" placeholder="Join our mailing list" />
                <button type="submit">Subscribe</button>
            </form>
            <form action="/book" method="post">
                <h2>Consultation Booking</h2>
                <span class="fee">Fee: $150 per session</span>
            </form>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        _clean_html_fragment(soup)
        text = soup.get_text(separator="\n")

        self.assertNotIn("Search catalog", text)
        self.assertNotIn("Join our mailing list", text)
        self.assertIn("Consultation Booking", text)
        self.assertIn("Fee: $150 per session", text)

    # -------------------------------------------------------------------------
    # Fix 2: Safe, Bounded Cookie-Banner Removal
    # -------------------------------------------------------------------------

    def test_cookie_banner_at_beginning_of_page(self):
        """Cookie banner at the start is removed and following content is preserved."""
        content = (
            "We use cookies to enhance your browsing experience. By using our site, you accept all cookies.\n\n"
            "Welcome to Lumina Skincare.\n\n"
            "Product: Rosehip Face Oil\nPrice: $34.00"
        )
        cleaned, is_blocked, _ = _clean_page_content(content)
        self.assertFalse(is_blocked)
        self.assertNotIn("enhance your browsing experience", cleaned)
        self.assertIn("Welcome to Lumina Skincare.", cleaned)
        self.assertIn("Product: Rosehip Face Oil", cleaned)
        self.assertIn("Price: $34.00", cleaned)

    def test_cookie_banner_in_middle_of_page(self):
        """Cookie banner in the middle is removed while surrounding text is preserved."""
        content = (
            "Header: Autumn Collection Now Live.\n\n"
            "This website uses cookies for analytics. Please choose your preferences. Accept all\n\n"
            "Featured Item: Velvet Matte Lip Crayon\nPrice: $22.00\nDiscount: 15% off at checkout."
        )
        cleaned, _, _ = _clean_page_content(content)
        self.assertNotIn("analytics", cleaned)
        self.assertNotIn("Accept all", cleaned)
        self.assertIn("Autumn Collection Now Live.", cleaned)
        self.assertIn("Velvet Matte Lip Crayon", cleaned)
        self.assertIn("$22.00", cleaned)

    def test_cookie_informational_content_preserved(self):
        """Ordinary informational text mentioning cookies is not accidentally deleted."""
        content = (
            "Bakery Specials:\n\n"
            "We bake fresh chocolate chip cookies and oatmeal pastries every morning.\n\n"
            "Box of 6 cookies: $12.50.\n\n"
            "We accept credit cards, debit cards, and cash."
        )
        cleaned, _, _ = _clean_page_content(content)
        self.assertIn("We bake fresh chocolate chip cookies and oatmeal pastries every morning.", cleaned)
        self.assertIn("Box of 6 cookies: $12.50.", cleaned)
        self.assertIn("We accept credit cards", cleaned)

    def test_cookie_banner_surrounded_by_prices(self):
        """Product prices and offers appearing before and after cookie banner survive."""
        content = (
            "Item Alpha: $49.00 (In Stock)\n\n"
            "Our website uses cookies to improve performance. Manage cookie preferences.\n\n"
            "Item Beta: $89.00 (Coupon: SAVE20 for $20 off)"
        )
        cleaned, _, _ = _clean_page_content(content)
        self.assertIn("Item Alpha: $49.00 (In Stock)", cleaned)
        self.assertIn("Item Beta: $89.00 (Coupon: SAVE20 for $20 off)", cleaned)

    def test_page_with_no_cookie_banner(self):
        """Page with no cookie banner is left untouched."""
        content = (
            "About Our Company\n\n"
            "We provide enterprise AI automation and data intelligence systems for modern teams."
        )
        cleaned, _, _ = _clean_page_content(content)
        self.assertEqual(cleaned, content)

    # -------------------------------------------------------------------------
    # Fix 3: Conservative Duplicate-Content Cleaning
    # -------------------------------------------------------------------------

    def test_conservative_deduplication_removes_repeated_boilerplate(self):
        """Exact repeated boilerplate text is deduplicated on the same page."""
        text = (
            "Free nationwide shipping on all orders over $50 with 30-day hassle-free returns.\n\n"
            "Product Overview: Ergonomic Office Chair with lumbar support and breathable mesh.\n\n"
            "Free nationwide shipping on all orders over $50 with 30-day hassle-free returns.\n\n"
            "Customer Review: High quality materials and quick delivery to our office.\n\n"
            "Free nationwide shipping on all orders over $50 with 30-day hassle-free returns."
        )
        deduped = _deduplicate_paragraphs_conservatively(text)
        self.assertEqual(deduped.count("Free nationwide shipping on all orders over $50"), 1)
        self.assertIn("Ergonomic Office Chair with lumbar support", deduped)
        self.assertIn("Customer Review: High quality materials", deduped)

    def test_conservative_deduplication_preserves_repeated_prices_for_distinct_items(self):
        """Repeated prices across different products on the same page are preserved."""
        text = (
            "Product A: Matte Hydrating Primer.\nPrice: $25.00\n\n"
            "Product B: Dewy Finish Setting Spray.\nPrice: $25.00\n\n"
            "Product C: Waterproof Precision Eyeliner.\nPrice: $25.00"
        )
        deduped = _deduplicate_paragraphs_conservatively(text)
        self.assertIn("Product A: Matte Hydrating Primer.", deduped)
        self.assertIn("Product B: Dewy Finish Setting Spray.", deduped)
        self.assertIn("Product C: Waterproof Precision Eyeliner.", deduped)
        self.assertEqual(deduped.count("Price: $25.00"), 3)

    def test_conservative_deduplication_preserves_short_headings(self):
        """Short headings (< 40 chars) are preserved even if repeated across sections."""
        text = (
            "Features\n\n"
            "Cloud synchronization and real-time multiplayer editing support.\n\n"
            "Features\n\n"
            "Automatic daily backups with AES-256 encryption at rest."
        )
        deduped = _deduplicate_paragraphs_conservatively(text)
        self.assertEqual(deduped.count("Features"), 2)
        self.assertIn("Cloud synchronization", deduped)
        self.assertIn("Automatic daily backups", deduped)

    # -------------------------------------------------------------------------
    # Fix 4: Reduce False Positives in Blocked-Page Detection
    # -------------------------------------------------------------------------

    def test_genuine_challenge_page_detected_as_blocked(self):
        """Clear challenge pages are accurately classified as blocked."""
        challenge_1 = "Attention Required! | Cloudflare. Please verify you are a human to continue."
        self.assertTrue(_is_blocked_content(challenge_1))

        challenge_2 = "403 Forbidden. Access Denied. Cloudflare Ray ID: 89ab12cd34ef."
        self.assertTrue(_is_blocked_content(challenge_2))

        challenge_3 = "Just a moment... Enable JavaScript and cookies to continue. cf-challenge"
        self.assertTrue(_is_blocked_content(challenge_3))

    def test_substantive_content_mentioning_security_terms_not_blocked(self):
        """Substantive business pages mentioning CAPTCHA, Cloudflare, or access terms are not blocked."""
        # 1. Page with reCAPTCHA badge disclaimer in footer (> 1500 chars)
        footer_page = (
            "Enterprise Marketing Intelligence Suite\n\n"
            + ("Our platform aggregates competitive signals across digital channels, ad copy, and pricing tables. " * 25)
            + "\n\nThis form is protected by reCAPTCHA and the Google Privacy Policy and Terms of Service apply."
        )
        self.assertGreater(len(footer_page), 1500)
        self.assertFalse(_is_blocked_content(footer_page))

        # 2. Terms of Service stating access restrictions
        terms_page = (
            "Terms of Service and Acceptable Use Policy\n\n"
            + ("Users must not reverse engineer the service or attempt unauthorized intrusion. " * 30)
            + "\n\nAccess denied to unauthorized accounts or users found violating compliance standards."
        )
        self.assertGreater(len(terms_page), 1500)
        self.assertFalse(_is_blocked_content(terms_page))

    def test_homepage_service_blocking_detection_protects_legitimate_pages(self):
        """_is_blocked_html in homepage_service avoids false positives on large pages."""
        # Substantive homepage HTML mentioning recaptcha in script or footer
        legitimate_html = (
            "<!DOCTYPE html><html><head><title>Apex Software - Cloud ERP</title>"
            "<script src='https://www.google.com/recaptcha/api.js'></script></head>"
            "<body><h1>Modern Enterprise Resource Planning</h1>"
            + ("<p>Streamline your supply chain, inventory management, and financial reporting with our platform.</p>" * 60)
            + "<footer>Protected by reCAPTCHA. Cloudflare Ray ID: 12345</footer></body></html>"
        )
        self.assertGreater(len(legitimate_html), 4000)
        self.assertFalse(_is_blocked_html(legitimate_html))

        # True Cloudflare challenge page HTML
        challenge_html = "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>Please verify you are a human</body></html>"
        self.assertTrue(_is_blocked_html(challenge_html))

    # -------------------------------------------------------------------------
    # Fix 5: Review Content Extraction Fallbacks
    # -------------------------------------------------------------------------

    def test_extract_best_content_prefers_complete_markdown(self):
        """Complete markdown is preferred over text or HTML."""
        item = {
            "markdown": "# Organic Face Balm\n\nFull organic balm with shea butter and calendula extract. Price: $28.",
            "text": "Alternative text representation",
            "html": "<p>Alternative HTML representation</p>",
        }
        extracted = _extract_best_content(item)
        self.assertEqual(extracted, item["markdown"])

    def test_extract_best_content_falls_back_when_markdown_empty(self):
        """When markdown is empty, falls back to text representation."""
        item = {
            "markdown": "",
            "text": "Clean natural makeup products with dermatologist approval. Over 100 shades available.",
            "html": "<p>HTML representation</p>",
        }
        extracted = _extract_best_content(item)
        self.assertEqual(extracted, item["text"])

    def test_extract_best_content_falls_back_to_html_when_text_sparse(self):
        """When text/markdown is a stub (< 50 chars), falls back to rich HTML."""
        item = {
            "markdown": "Title Only",  # 10 chars, too short
            "text": "Title Only",
            "html": (
                "<div class='product'>"
                "<h1>Natural Peptide Serum</h1>"
                "<p class='description'>Clinically proven to reduce fine lines and boost skin firmness within 14 days.</p>"
                "<span class='price'>Price: $45.00</span>"
                "</div>"
            ),
        }
        extracted = _extract_best_content(item)
        self.assertIn("Natural Peptide Serum", extracted)
        self.assertIn("reduce fine lines", extracted)
        self.assertIn("$45.00", extracted)

    def test_extract_best_content_all_empty(self):
        """When all representations are empty, returns empty string."""
        item = {"markdown": "", "text": "", "content": "", "html": ""}
        extracted = _extract_best_content(item)
        self.assertEqual(extracted, "")

    def test_extract_best_content_does_not_concatenate_duplicates(self):
        """Does not blindly concatenate multiple representations."""
        item = {
            "markdown": "Detailed product description for competitive marketing intelligence analysis.",
            "text": "Detailed product description for competitive marketing intelligence analysis.",
            "content": "Detailed product description for competitive marketing intelligence analysis.",
        }
        extracted = _extract_best_content(item)
        # Should appear exactly once, not three times
        self.assertEqual(extracted.count("Detailed product description"), 1)

    # -------------------------------------------------------------------------
    # Fix 6: Preserve Honest HTTP Status Metadata
    # -------------------------------------------------------------------------

    async def test_honest_http_status_metadata_preservation(self):
        """Crawler preserves genuine status codes and does not fabricate 200 or 403."""
        item_200 = {
            "url": "https://example.com/item1",
            "markdown": "Substantive page content with detailed product descriptions and valid pricing.",
            "crawl": {"httpStatusCode": 200},
        }

        item_none_status = {
            "url": "https://example.com/item2",
            "markdown": "Another substantive page with complete specifications and service outlines.",
            "crawl": {"httpStatusCode": None},
        }

        item_404 = {
            "url": "https://example.com/item3",
            "markdown": "Page Not Found",
            "crawl": {"httpStatusCode": 404},
        }

        mock_items = [item_200, item_none_status, item_404]

        with patch("backend.app.services.website_content_crawler_service.settings") as mock_settings:
            mock_settings.APIFY_API_TOKEN.get_secret_value.return_value = "mock_valid_token"

            with patch("backend.app.services.website_content_crawler_service._run_actor_sync") as mock_run:
                mock_run.return_value = (mock_items, "run-xyz-123", None)

                result = await crawl_selected_pages(
                    pages=[
                        "https://example.com/item1",
                        "https://example.com/item2",
                        "https://example.com/item3",
                    ],
                    website_url="https://example.com",
                )

                scraped_by_url = {p.url: p for p in result.scraped_pages}
                self.assertIn("https://example.com/item1", scraped_by_url)
                self.assertEqual(scraped_by_url["https://example.com/item1"].status_code, 200)

                self.assertIn("https://example.com/item2", scraped_by_url)
                # CRITICAL: Must be None, NOT 200
                self.assertIsNone(scraped_by_url["https://example.com/item2"].status_code)

                failed_by_url = {f.url: f for f in result.failed_pages}
                self.assertIn("https://example.com/item3", failed_by_url)
                self.assertEqual(failed_by_url["https://example.com/item3"].status_code, 404)


if __name__ == "__main__":
    unittest.main()
