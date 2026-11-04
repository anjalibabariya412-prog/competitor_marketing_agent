"""Tests for Phase 4 (Geo/Redirect context) and Phase 5 (LLM Step Robustness)."""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import backend.app.main  # Ensure standard import ordering
from backend.app.schemas.enums import OfferingType, PageType
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.business_overview_service import (
    BusinessOverview,
    TargetAudienceInfo,
    detect_regional_context,
    generate_business_overview,
    synthesize_emergency_overview,
)


class TestPhase4And5GeoAndRobustness(unittest.IsolatedAsyncioTestCase):
    """Test suite for Phase 4 and Phase 5 features."""

    def test_detect_regional_context_tld(self) -> None:
        """Domain with ccTLD .in detects India."""
        note = detect_regional_context("https://swissbeauty.in", [])
        self.assertIsNotNone(note)
        self.assertIn("India (IN)", note)

    def test_detect_regional_context_path(self) -> None:
        """URL with /en-us/ prefix detects US."""
        pages = [ScrapedPage(url="https://lenskart.com/en-us/products", content="Sample glasses")]
        note = detect_regional_context("https://lenskart.com", pages)
        self.assertIsNotNone(note)
        self.assertIn("United States (US)", note)

    def test_synthesize_emergency_overview(self) -> None:
        """Emergency overview synthesis produces low-confidence, unverified overview without crashing."""
        form = {
            "business_name_and_description": "Acme Tools - Manufacturer of industrial anvils",
            "product_or_service": "Anvils",
            "target_audience": "Blacksmiths and factories",
        }
        overview = synthesize_emergency_overview(form, "https://acmetools.com", reason="Target site blocked scraper")
        self.assertEqual(overview.business_name, "Acme Tools")
        self.assertEqual(overview.website_url, "https://acmetools.com")
        self.assertEqual(len(overview.products_and_services), 1)
        self.assertIsNone(overview.products_and_services[0].source_url)
        self.assertFalse(overview.target_audience.is_explicit)
        self.assertIn("Target site blocked scraper", overview.information_gaps[0].observation)
        self.assertEqual(len(overview.sources_used), 0)

    async def test_emergency_overview_gated_by_setting(self) -> None:
        """Emergency overview triggers on insufficient content only when ENABLE_EMERGENCY_OVERVIEW is True."""
        form = {"business_name_and_description": "Acme Tools", "product_or_service": "Anvils"}
        empty_pages = [ScrapedPage(url="https://acmetools.com", content="Too short")]

        # Default (False) returns insufficient_content
        with patch("backend.app.services.business_overview_service.settings.ENABLE_EMERGENCY_OVERVIEW", False):
            res_default = await generate_business_overview(empty_pages, website_url="https://acmetools.com", business_form=form)
            self.assertEqual(res_default.status, "insufficient_content")
            self.assertIsNone(res_default.business_overview)

        # Enabled (True) returns success with emergency overview
        with patch("backend.app.services.business_overview_service.settings.ENABLE_EMERGENCY_OVERVIEW", True):
            res_enabled = await generate_business_overview(empty_pages, website_url="https://acmetools.com", business_form=form)
            self.assertEqual(res_enabled.status, "success")
            self.assertIsNotNone(res_enabled.business_overview)
            self.assertEqual(res_enabled.business_overview.business_name, "Acme Tools")

    async def test_json_repair_recovers_parsed_from_raw_content(self) -> None:
        """When structured parse returns None, local JSON extraction recovers BusinessOverview from message content."""
        sample_page = ScrapedPage(url="https://example.com/about", content="Comprehensive content about Acme Tools." * 20)

        valid_data = {
            "business_name": "Acme Tools",
            "website_url": "https://example.com",
            "business_description": "Makers of quality tools.",
            "products_and_services": [],
            "target_audience": {
                "description": "Craftspeople",
                "is_explicit": True,
                "source_url": "https://example.com/about",
            },
            "positioning_and_value_propositions": [],
            "information_gaps": [],
            "sources_used": ["https://example.com/about"],
        }
        json_str = f"Here is the overview: ```json\n{json.dumps(valid_data)}\n```"

        mock_msg = MagicMock()
        mock_msg.parsed = None
        mock_msg.content = json_str

        mock_choice = MagicMock()
        mock_choice.message = mock_msg

        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]
        mock_completion.usage = MagicMock(prompt_tokens=100, completion_tokens=50, total_tokens=150)

        with patch("backend.app.services.business_overview_service.AsyncOpenAI") as mock_openai_cls, \
             patch("backend.app.services.business_overview_service.settings.OPENAI_API_KEY") as mock_key:
            mock_key.get_secret_value.return_value = "sk-test"
            mock_client = AsyncMock()
            mock_client.beta.chat.completions.parse.return_value = mock_completion
            mock_openai_cls.return_value = mock_client

            result = await generate_business_overview(
                [sample_page],
                website_url="https://example.com",
            )
            self.assertEqual(result.status, "success")
            self.assertIsNotNone(result.business_overview)
            self.assertEqual(result.business_overview.business_name, "Acme Tools")


if __name__ == "__main__":
    unittest.main()

