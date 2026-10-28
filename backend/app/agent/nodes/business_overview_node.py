"""LangGraph Business Overview Node for the Competitor Marketing Agent.

This node is the fourth operational stage in the workflow pipeline.
It consumes the validated business intake form and crawled website content from
`AgentState`, invokes the business overview generation service (which uses OpenAI
with strict evidence-grounding to synthesize company offerings, positioning,
target audience, and information gaps without hallucination), and stores the
resulting `overview_result` and `business_overview` in `AgentState`.
"""

from __future__ import annotations

import logging
from typing import Any
import urllib.parse

from ..state import AgentState
from backend.app.schemas.business_form import BusinessForm
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.business_overview_service import (
    BusinessOverview,
    BusinessOverviewResult,
    generate_business_overview,
)
from backend.app.services.website_content_crawler_service import WebsiteCrawlResult

logger = logging.getLogger(__name__)


async def business_overview_node(state: AgentState) -> dict[str, Any]:
    """Execute the Business Overview stage of the Competitor Marketing Agent workflow.

    Reads `business_form`, `crawl_result`, and `scraped_pages` from `state`,
    resolves the website URL and crawled content, and calls `generate_business_overview`
    to produce a structured, evidence-based business profile without fabricating facts.

    Args:
        state: Shared LangGraph `AgentState` containing at minimum `business_form`
            and website crawling output (`crawl_result` or `scraped_pages`).

    Returns:
        Partial state dictionary with updated fields:
        - `overview_result`: Full `BusinessOverviewResult` instance.
        - `business_overview`: Generated `BusinessOverview` model (or None on failure).
        - `current_step`: "business_overview".
        - `workflow_status`: "completed" on success, or "failed" if overview generation
          fails, insufficient content is present, or an error occurs.
        - `error_message`: Set on failure.
        - `errors`: Cumulative list of error messages if any occurred.
    """
    logger.info("Entering Business Overview Node.")

    # 1. Validate state input structure
    if not isinstance(state, dict):
        error_msg = f"Business Overview Node expected dict/AgentState, received {type(state).__name__}."
        logger.error(error_msg)
        return {
            "current_step": "business_overview",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": [error_msg],
        }

    # 2. Extract and validate business_form (required input)
    business_form = state.get("business_form")
    if business_form is None:
        error_msg = (
            "Business Overview Node failed: 'business_form' is missing from AgentState. "
            "Original business intake data is required."
        )
        logger.warning(error_msg)
        failed_result = BusinessOverviewResult(
            status="failed",
            business_overview=None,
            website_url="https://unknown.com",
            total_pages_analyzed=0,
            total_findings=0,
            model_used=None,
            error_message=error_msg,
        )
        existing_errors = list(state.get("errors") or [])
        existing_errors.append(error_msg)
        return {
            "overview_result": failed_result,
            "business_overview": None,
            "current_step": "business_overview",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": existing_errors,
        }

    # 3. Resolve target website_url
    website_url = ""
    if isinstance(business_form, BusinessForm):
        website_url = business_form.business_website
    elif isinstance(business_form, dict):
        website_url = business_form.get("business_website", "")

    crawl_result = state.get("crawl_result")
    if not website_url and crawl_result is not None:
        if hasattr(crawl_result, "website_url") and crawl_result.website_url:
            website_url = crawl_result.website_url
        elif isinstance(crawl_result, dict):
            website_url = crawl_result.get("website_url", "")

    clean_url = website_url.strip() if website_url else ""

    # 4. Extract and normalize crawl_input (from crawl_result or scraped_pages)
    raw_scraped_pages = state.get("scraped_pages")
    cleaned_scraped_pages: list[ScrapedPage] = []

    if isinstance(raw_scraped_pages, list):
        for item in raw_scraped_pages:
            if isinstance(item, ScrapedPage):
                cleaned_scraped_pages.append(item)
            elif isinstance(item, dict):
                try:
                    cleaned_scraped_pages.append(ScrapedPage(**item))
                except Exception:
                    continue

    crawl_input: WebsiteCrawlResult | list[ScrapedPage]
    if isinstance(crawl_result, WebsiteCrawlResult) and crawl_result.scraped_pages:
        crawl_input = crawl_result
    elif cleaned_scraped_pages:
        crawl_input = cleaned_scraped_pages
    elif isinstance(crawl_result, WebsiteCrawlResult):
        crawl_input = crawl_result
    elif isinstance(crawl_result, dict):
        try:
            crawl_input = WebsiteCrawlResult(**crawl_result)
        except Exception:
            crawl_input = cleaned_scraped_pages
    else:
        crawl_input = cleaned_scraped_pages

    logger.info(
        "Business Overview Node initiating generation for website: '%s'.",
        clean_url,
    )

    # 5. Call existing business overview service
    try:
        overview_result = await generate_business_overview(
            crawl_input=crawl_input,
            website_url=clean_url or None,
        )
    except Exception as exc:
        logger.exception("Unexpected exception in business overview service for '%s': %s", clean_url, exc)
        error_msg = f"Business overview generation failed unexpectedly for '{clean_url}': {exc}"
        failed_result = BusinessOverviewResult(
            status="failed",
            business_overview=None,
            website_url=clean_url or "https://unknown.com",
            total_pages_analyzed=len(cleaned_scraped_pages),
            total_findings=0,
            model_used=None,
            error_message=error_msg,
        )
        existing_errors = list(state.get("errors") or [])
        existing_errors.append(error_msg)
        return {
            "overview_result": failed_result,
            "business_overview": None,
            "current_step": "business_overview",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": existing_errors,
        }

    # 6. Handle service failure or insufficient content
    if overview_result.status != "success" or overview_result.business_overview is None:
        error_msg = (
            f"Business overview generation could not complete for '{clean_url}': "
            f"status '{overview_result.status}' - {overview_result.error_message or 'No overview generated.'}"
        )
        logger.warning("Business Overview Node failure: %s", error_msg)
        existing_errors = list(state.get("errors") or [])
        existing_errors.append(error_msg)
        return {
            "overview_result": overview_result,
            "business_overview": None,
            "current_step": "business_overview",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": existing_errors,
        }

    # 7. Successful generation
    logger.info(
        "Business Overview Node completed successfully for '%s': %d findings, %d products analyzed.",
        clean_url,
        overview_result.total_findings,
        len(overview_result.business_overview.products_and_services),
    )

    return {
        "overview_result": overview_result,
        "business_overview": overview_result.business_overview,
        "current_step": "business_overview",
        "workflow_status": "completed",
    }

