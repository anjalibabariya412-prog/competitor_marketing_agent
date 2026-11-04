"""Unit tests for Phase 1 content extraction hardening.

Verifies:
1. Layered fallback extraction on synthetic Next.js SPA HTML (e.g. Lenskart shell).
2. JSON-LD schema, meta description, and heading recovery.
3. Cookie consent and boilerplate banner removal.
4. Weak-signal vs full-page prompt formatting in business overview service.
"""

from __future__ import annotations

import unittest

import backend.app.main  # Ensure canonical import order
from backend.app.schemas.enums import PageType
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.content_validation import (
    clean_boilerplate,
    extract_structured_fallback_text,
    extract_text_from_html,
    is_content_adequate,
)
from backend.app.services.business_overview_service import (
    _format_pages_for_prompt,
    _extract_usable_pages,
)
from backend.app.services.website_content_crawler_service import _extract_text_from_html


LENSKART_SYNTHETIC_SPA_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Lenskart.com: Buy Eyeglasses, Sunglasses &amp; Contact Lenses Online</title>
    <meta name="description" content="Shop eyeglasses, sunglasses, and contact lenses online at Lenskart. Free home eye test, 1-year warranty, and wide range of frames.">
    <meta property="og:title" content="Lenskart Eyewear - Buy Frames &amp; Sunglasses">
    <meta property="og:description" content="Discover India's top eyewear brand with premium optical lenses and trendy frames.">
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Organization",
                "name": "Lenskart",
                "legalName": "Lenskart Solutions Pvt. Ltd.",
                "description": "Leading omni-channel eyewear retailer specializing in prescription eyeglasses, blue-cut computer glasses, polarized sunglasses, and contact lenses.",
                "slogan": "Do More, Be More",
                "knowsAbout": ["Prescription Eyeglasses", "Computer Glasses", "Polarized Sunglasses", "Contact Lenses", "Free Home Eye Test"]
            },
            {
                "@type": "Product",
                "name": "Vincent Chase Polarized Aviator",
                "brand": {"@type": "Brand", "name": "Vincent Chase"},
                "description": "Premium polarized metallic frame sunglasses with 100% UV400 protection.",
                "offers": {
                    "@type": "Offer",
                    "price": "1999",
                    "priceCurrency": "INR"
                }
            },
            {
                "@type": "Product",
                "name": "John Jacobs Rimless Titanium Eyeglasses",
                "brand": {"@type": "Brand", "name": "John Jacobs"},
                "description": "Ultra-lightweight Japanese titanium prescription optical frame.",
                "offers": {
                    "@type": "Offer",
                    "price": "3500",
                    "priceCurrency": "INR"
                }
            }
        ]
    }
    </script>
</head>
<body>
    <div id="__next">
        <div class="cookie-notice-banner">
            We use cookies to improve your browsing experience. By clicking Accept Cookies, you agree to our cookie policy.
            <button class="accept-btn">Accept All Cookies</button>
        </div>
        <!-- Client-side SPA skeleton: no server-rendered paragraph text -->
        <main class="spa-container">
            <h1>Revolutionizing Eyewear with Precision Optics</h1>
            <h2>Trending Collections &amp; Computer Glasses</h2>
            <div class="cta-actions">
                <button type="button" class="btn primary-btn">Book Free Home Eye Test</button>
                <button type="button" class="btn secondary-btn">Try 3D Virtual AR Try-On</button>
            </div>
            <div class="featured-grid">
                <img src="/img/vincent.jpg" alt="Vincent Chase Blue Light Blocking Glasses for Screen Use" />
                <img src="/img/johnj.jpg" alt="John Jacobs Italian Acetate Designer Eyewear" />
            </div>
        </main>
        <footer>
            <p>Copyright © 2024 Lenskart Solutions Pvt. Ltd. All rights reserved.</p>
        </footer>
    </div>
</body>
</html>
"""


class TestPhase1ContentExtraction(unittest.TestCase):
    """Test suite for Phase 1 content extraction improvements."""

    def test_clean_boilerplate_removes_cookie_and_copyright_noise(self) -> None:
        """Verify clean_boilerplate strips cookie banners and copyright footers while preserving content."""
        sample_text = (
            "We use cookies to ensure you get the best experience on our website. Accept All Cookies.\n\n"
            "Lenskart is India's leading eyewear platform offering over 5,000 styles.\n\n"
            "Copyright © 2024 Lenskart Solutions Pvt. Ltd. All rights reserved."
        )
        cleaned = clean_boilerplate(sample_text)
        self.assertNotIn("We use cookies", cleaned)
        self.assertNotIn("Accept All Cookies", cleaned)
        self.assertNotIn("All rights reserved", cleaned)
        self.assertIn("Lenskart is India's leading eyewear platform", cleaned)

    def test_structured_fallback_extraction_from_spa_html(self) -> None:
        """Verify extract_structured_fallback_text extracts JSON-LD, meta, and headings from SPA shell."""
        fallback = extract_structured_fallback_text(LENSKART_SYNTHETIC_SPA_HTML)
        self.assertGreater(len(fallback), 400)
        # Check Organization JSON-LD recovery
        self.assertIn("Lenskart Solutions Pvt. Ltd.", fallback)
        self.assertIn("Leading omni-channel eyewear retailer", fallback)
        self.assertIn("Prescription Eyeglasses", fallback)
        # Check Product JSON-LD recovery
        self.assertIn("Vincent Chase Polarized Aviator", fallback)
        self.assertIn("1999 INR", fallback)
        self.assertIn("John Jacobs Rimless Titanium Eyeglasses", fallback)
        # Check Headings recovery
        self.assertIn("Revolutionizing Eyewear with Precision Optics", fallback)
        # Check Buttons / Actions recovery
        self.assertIn("Book Free Home Eye Test", fallback)
        # Check Alt texts recovery
        self.assertIn("Vincent Chase Blue Light Blocking Glasses", fallback)

    def test_extract_text_from_html_integrates_layered_fallback_and_passes_adequacy(self) -> None:
        """Verify extract_text_from_html produces substantive text that passes adequacy checks."""
        extracted = extract_text_from_html(LENSKART_SYNTHETIC_SPA_HTML)
        self.assertGreaterEqual(len(extracted), 500)
        # Verify boilerplate was cleaned
        self.assertNotIn("We use cookies", extracted)
        self.assertNotIn("Copyright © 2024", extracted)
        # Verify content adequacy
        is_adequate, reason = is_content_adequate(
            extracted,
            url="https://www.lenskart.com/",
            page_type=PageType.HOME,
        )
        self.assertTrue(is_adequate, f"Expected adequate content, got {reason}")

    def test_crawler_service_extract_text_from_html_layered_fallback(self) -> None:
        """Verify website_content_crawler_service._extract_text_from_html uses layered fallback."""
        extracted = _extract_text_from_html(LENSKART_SYNTHETIC_SPA_HTML)
        self.assertGreaterEqual(len(extracted), 500)
        self.assertIn("Lenskart Solutions Pvt. Ltd.", extracted)
        self.assertIn("Polarized Aviator", extracted)

    def test_format_pages_for_prompt_distinguishes_weak_signal_and_preserves_context(self) -> None:
        """Verify _format_pages_for_prompt marks pages < 300 chars as weak signal without dropping them."""
        full_page = ScrapedPage(
            url="https://www.lenskart.com/about-us",
            title="About Lenskart",
            content="Lenskart was founded with a mission to give vision to millions. " * 10,
            page_type=PageType.ABOUT,
        )
        short_page = ScrapedPage(
            url="https://www.lenskart.com/contact",
            title="Contact Support",
            content="Support email: care@lenskart.com | Phone: 1800-111-111",
            page_type=PageType.CONTACT,
        )
        formatted_text, included_pages = _format_pages_for_prompt([full_page, short_page])
        self.assertEqual(len(included_pages), 2)
        # Full page is formatted normally
        self.assertIn("--- Page 1: https://www.lenskart.com/about-us", formatted_text)
        # Short page is formatted under Weak Signal
        self.assertIn("Weak Signal: Partial content", formatted_text)
        self.assertIn("care@lenskart.com", formatted_text)
        self.assertIn("https://www.lenskart.com/contact", formatted_text)


if __name__ == "__main__":
    unittest.main()
