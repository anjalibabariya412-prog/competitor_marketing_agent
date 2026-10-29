"""LangGraph Website Crawling Node for the Competitor Marketing Agent.

This node is the third operational stage in the workflow pipeline.
It consumes selected pages from `AgentState`, calls the existing website
content crawler service (which utilizes the Apify Website Content Crawler
in zero-depth mode with content cleaning, boilerplate removal, and failure tracking),
and populates `crawl_result`, `scraped_pages`, and `failed_pages` in `AgentState`.
"""

from __future__ import annotations

import logging
from typing import Any
import urllib.parse

from ..state import AgentState
from backend.app.schemas.business_form import BusinessForm
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.page_selection_service import SelectedPage
from backend.app.services.sitemap_fallback_service import WebsiteURLDiscoveryResult
from backend.app.services.website_content_crawler_service import (
    FailedPageScrape,
    WebsiteCrawlResult,
    crawl_selected_pages,
)

logger = logging.getLogger(__name__)


async def website_crawling_node(state: AgentState) -> dict[str, Any]:
    """Execute the Website Crawling stage of the Competitor Marketing Agent workflow.

    Reads `selected_pages` from `state`, resolves the canonical `website_url`,
    and invokes `crawl_selected_pages` to scrape readable text and markdown
    content for the selected pages without site-wide crawling.

    Args:
        state: Shared LangGraph `AgentState` containing at minimum `selected_pages`.

    Returns:
        Partial state dictionary with updated fields:
        - `crawl_result`: Complete `WebsiteCrawlResult` metadata.
        - `scraped_pages`: `list[ScrapedPage]` successfully scraped pages.
        - `failed_pages`: `list[FailedPageScrape]` records of pages that failed.
        - `current_step`: "website_crawling".
        - `workflow_status`: "running" if usable content was obtained, or "failed" if
          no pages were selected, no usable content was scraped, or an unrecoverable error occurred.
        - `error_message`: Set on failure.
        - `errors`: Cumulative list of errors/advisories if any occurred.
    """
    logger.info("Entering Website Crawling Node.")

    # 1. Validate state input structure
    if not isinstance(state, dict):
        error_msg = f"Website Crawling Node expected dict/AgentState, received {type(state).__name__}."
        logger.error(error_msg)
        return {
            "current_step": "website_crawling",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": [error_msg],
        }

    # 2. Extract and validate selected_pages from state
    raw_selected_pages = state.get("selected_pages")
    cleaned_selected_pages: list[SelectedPage] = []

    if isinstance(raw_selected_pages, list):
        for item in raw_selected_pages:
            if isinstance(item, SelectedPage):
                cleaned_selected_pages.append(item)
            elif isinstance(item, dict):
                try:
                    cleaned_selected_pages.append(SelectedPage(**item))
                except Exception:
                    continue
            elif isinstance(item, str):
                cleaned_selected_pages.append(SelectedPage(url=item))

    # 3. Resolve target website_url for the crawler service
    website_url = ""
    discovery_result = state.get("discovery_result")
    if discovery_result is not None:
        if hasattr(discovery_result, "base_url") and discovery_result.base_url:
            website_url = discovery_result.base_url
        elif hasattr(discovery_result, "requested_url") and discovery_result.requested_url:
            website_url = discovery_result.requested_url
        elif isinstance(discovery_result, dict):
            website_url = discovery_result.get("base_url") or discovery_result.get("requested_url") or ""

    if not website_url:
        business_form = state.get("business_form")
        if isinstance(business_form, BusinessForm):
            website_url = business_form.business_website
        elif isinstance(business_form, dict):
            website_url = business_form.get("business_website", "")

    if not website_url and cleaned_selected_pages:
        parsed = urllib.parse.urlsplit(cleaned_selected_pages[0].url)
        if parsed.scheme and parsed.netloc:
            website_url = f"{parsed.scheme}://{parsed.netloc}"

    clean_url = website_url.strip() if website_url else ""

    # Handle missing or empty selected_pages
    if not cleaned_selected_pages:
        error_msg = (
            "Website Crawling Node failed: 'selected_pages' is missing or empty in AgentState. "
            "No selected pages available to crawl."
        )
        logger.warning(error_msg)
        failed_result = WebsiteCrawlResult(
            website_url=clean_url or "unknown",
            status="empty",
            scraped_pages=[],
            failed_pages=[],
            total_requested=0,
            total_scraped=0,
            total_failed=0,
            has_usable_content=False,
            error_message="Website Crawling Node received 0 selected pages.",
        )
        existing_errors = list(state.get("errors") or [])
        existing_errors.append(error_msg)
        return {
            "crawl_result": failed_result,
            "scraped_pages": [],
            "failed_pages": [],
            "current_step": "website_crawling",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": existing_errors,
        }

    logger.info(
        "Website Crawling Node processing %d selected page(s) for website: '%s'.",
        len(cleaned_selected_pages),
        clean_url,
    )

    # 4. Invoke the existing website content crawler service
    try:
        crawl_result = await crawl_selected_pages(
            pages=cleaned_selected_pages,
            website_url=clean_url,
        )
    except Exception as exc:
        logger.exception("Unexpected exception in website content crawler for '%s': %s", clean_url, exc)
        error_msg = f"Website crawling failed unexpectedly for '{clean_url}': {exc}"
        failed_result = WebsiteCrawlResult(
            website_url=clean_url or "unknown",
            status="failed",
            scraped_pages=[],
            failed_pages=[
                FailedPageScrape(url=p.url, error_message=error_msg, page_type=p.page_type)
                for p in cleaned_selected_pages
            ],
            total_requested=len(cleaned_selected_pages),
            total_scraped=0,
            total_failed=len(cleaned_selected_pages),
            has_usable_content=False,
            error_message=error_msg,
        )
        existing_errors = list(state.get("errors") or [])
        existing_errors.append(error_msg)
        return {
            "crawl_result": failed_result,
            "scraped_pages": [],
            "failed_pages": failed_result.failed_pages,
            "current_step": "website_crawling",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": existing_errors,
        }

    # 5. Handle complete crawl failure (0 scraped pages or no usable content)
    if not crawl_result.has_usable_content or crawl_result.total_scraped == 0 or crawl_result.status in ("failed", "empty"):
        if getattr(crawl_result, "actor_status", None) == "TIMED-OUT":
            error_msg = (
                f"Website crawling timed out and yielded 0 usable pages for '{clean_url}': "
                f"{crawl_result.error_message or 'Actor timed out before returning any readable content.'}"
            )
        elif getattr(crawl_result, "actor_status", None) in ("READY", "RUNNING", "TIMING-OUT", "ABORTING"):
            error_msg = (
                f"Website crawling remained nonterminal ('{crawl_result.actor_status}') for '{clean_url}': "
                f"{crawl_result.error_message}"
            )
        else:
            error_msg = (
                f"Website crawling yielded 0 usable pages for '{clean_url}': "
                f"{crawl_result.error_message or 'All requested pages failed during scraping.'}"
            )
        logger.warning("Website Crawling Node failure: %s", error_msg)
        existing_errors = list(state.get("errors") or [])
        existing_errors.append(error_msg)
        return {
            "crawl_result": crawl_result,
            "scraped_pages": [],
            "failed_pages": crawl_result.failed_pages,
            "current_step": "website_crawling",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": existing_errors,
        }

    # 6. Successful or partial crawl with usable content
    logger.info(
        "Website Crawling Node completed for '%s': status='%s', scraped=%d/%d, failed=%d.",
        clean_url,
        crawl_result.status,
        crawl_result.total_scraped,
        crawl_result.total_requested,
        crawl_result.total_failed,
    )

    update: dict[str, Any] = {
        "crawl_result": crawl_result,
        "scraped_pages": crawl_result.scraped_pages,
        "failed_pages": crawl_result.failed_pages,
        "current_step": "website_crawling",
        "workflow_status": "running",
    }

    if crawl_result.status == "partial" or crawl_result.total_failed > 0:
        existing_errors = list(state.get("errors") or [])
        advisory = (
            f"Website crawling advisory: {crawl_result.total_failed} of {crawl_result.total_requested} "
            f"pages failed to scrape for '{clean_url}'."
        )
        if getattr(crawl_result, "actor_status", None) == "TIMED-OUT":
            advisory += f" (Actor timed out; {crawl_result.total_scraped} partial pages recovered)."
        existing_errors.append(advisory)
        update["errors"] = existing_errors

    return update

