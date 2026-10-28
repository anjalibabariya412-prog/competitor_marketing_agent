"""Shared LangGraph state schema for the Competitor Marketing Agent.

Defines the centralized state passed across workflow nodes from initial business
intake through URL discovery, page selection, website content crawling, and
evidence-based business overview generation.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from backend.app.schemas.business_form import BusinessForm
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.business_overview_service import (
    BusinessOverview,
    BusinessOverviewResult,
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


class AgentState(TypedDict, total=False):
    """Shared state dictionary tracked and updated across LangGraph workflow nodes.

    Configured with `total=False` to support partial state updates returned by
    individual graph nodes and allow incremental population across pipeline stages.

    Workflow Stages:
        A. User Input: Preserves original business intake supplied by user.
        B. URL Discovery: Discovered candidates and sitemap fallback metadata.
        C. Page Selection: Evaluated candidate URLs prioritized for deep analysis.
        D. Website Crawling: Readable scraped text/markdown and failure records.
        E. Business Overview: Structured evidence-based company summary and findings.
        F. Workflow Status & Errors: Minimal stage coordination and diagnostic errors.
    """

    # ---------------------------------------------------------------------------
    # A. User Input
    # ---------------------------------------------------------------------------
    # Validated business intake form as single source of truth for all user data
    business_form: BusinessForm | None

    # ---------------------------------------------------------------------------
    # B. URL Discovery
    # ---------------------------------------------------------------------------
    # Full discovery result including homepage status and sitemap fallback metadata
    discovery_result: WebsiteURLDiscoveryResult | None

    # Combined candidate URLs extracted from homepage HTML and XML sitemaps
    candidate_urls: list[CandidateURL] | None

    # ---------------------------------------------------------------------------
    # C. Page Selection
    # ---------------------------------------------------------------------------
    # Structured selection result including selection method and diagnostic info
    selection_result: PageSelectionResult | None

    # Selected candidate pages prioritized for competitor marketing intelligence
    selected_pages: list[SelectedPage] | None

    # ---------------------------------------------------------------------------
    # D. Website Crawling
    # ---------------------------------------------------------------------------
    # Structured crawl result including status, run ID, and timing
    crawl_result: WebsiteCrawlResult | None

    # Successfully scraped pages with cleaned content, page types, and metadata
    scraped_pages: list[ScrapedPage] | None

    # Pages that failed during scraping or returned unusable content
    failed_pages: list[FailedPageScrape] | None

    # ---------------------------------------------------------------------------
    # E. Business Overview
    # ---------------------------------------------------------------------------
    # Complete result from LLM-based business overview generation
    overview_result: BusinessOverviewResult | None

    # Generated evidence-based overview, offerings, audience, and key findings
    business_overview: BusinessOverview | None

    # ---------------------------------------------------------------------------
    # F. Workflow Status and Errors
    # ---------------------------------------------------------------------------
    # Identifier of the active or most recently completed workflow node
    current_step: str | None

    # Overall workflow execution status ('pending', 'running', 'completed', 'failed')
    workflow_status: Literal["pending", "running", "completed", "failed"] | str | None

    # Primary error message if a critical failure occurs during execution
    error_message: str | None

    # Cumulative list of non-fatal warnings or diagnostic error messages
    errors: list[str] | None


__all__ = ["AgentState"]
