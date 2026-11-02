"""LangGraph Website Crawling Node for the Competitor Marketing Agent.

This node is the third operational stage in the workflow pipeline.
It consumes selected pages from `AgentState`, calls the existing website
content crawler service (which utilizes the Apify Website Content Crawler
in zero-depth mode with content cleaning, boilerplate removal, and failure tracking),
and populates `crawl_result`, `scraped_pages`, and `failed_pages` in `AgentState`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
import urllib.parse

from ..state import AgentState
from backend.app.core.config import settings
from backend.app.schemas.business_form import BusinessForm
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.page_selection_service import SelectedPage
from backend.app.services.sitemap_fallback_service import WebsiteURLDiscoveryResult
from backend.app.services.website_content_crawler_service import (
    MIN_PAGE_CONTENT_CHARS,
    FailedPageScrape,
    WebsiteCrawlResult,
    crawl_selected_pages,
)

logger = logging.getLogger(__name__)


def _normalize_url_for_merge(url: str) -> str:
    """Normalize URL for deduplication when merging homepage into scraped pages."""
    cleaned = url.strip().lower().rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(cleaned)
        netloc = parsed.netloc
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = parsed.path.rstrip("/")
        return urllib.parse.urlunsplit((parsed.scheme, netloc, path, parsed.query, ""))
    except Exception:
        return cleaned


def _merge_homepage_into_scraped_pages(
    homepage_scraped_page: ScrapedPage | None,
    scraped_pages: list[ScrapedPage],
) -> list[ScrapedPage]:
    """Prepend homepage_scraped_page to scraped_pages if usable and not already present."""
    if homepage_scraped_page is None:
        return list(scraped_pages)

    if not homepage_scraped_page.content or not homepage_scraped_page.content.strip():
        return list(scraped_pages)

    crawled_urls_normalized = {_normalize_url_for_merge(p.url) for p in scraped_pages}
    hp_url_normalized = _normalize_url_for_merge(homepage_scraped_page.url)

    if hp_url_normalized not in crawled_urls_normalized:
        return [homepage_scraped_page] + list(scraped_pages)
    return list(scraped_pages)


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
    homepage_scraped_page: ScrapedPage | None = state.get("homepage_scraped_page")  # type: ignore[assignment]

    # Handle missing or empty selected_pages
    if not cleaned_selected_pages:
        error_msg = (
            "Website Crawling Node failed: 'selected_pages' is missing or empty in AgentState. "
            "No selected pages available to crawl."
        )
        logger.warning(error_msg)
        final_scraped_pages = _merge_homepage_into_scraped_pages(homepage_scraped_page, [])
        failed_result = WebsiteCrawlResult(
            website_url=clean_url or "unknown",
            status="empty",
            scraped_pages=final_scraped_pages,
            failed_pages=[],
            total_requested=0,
            total_scraped=len(final_scraped_pages),
            total_failed=0,
            has_usable_content=bool(final_scraped_pages),
            error_message="Website Crawling Node received 0 selected pages.",
        )
        existing_errors = list(state.get("errors") or [])
        existing_errors.append(error_msg)

        if final_scraped_pages:
            logger.info(
                "Preserving homepage (%d chars) despite 0 selected pages for '%s'.",
                len(final_scraped_pages[0].content),
                clean_url,
            )
            return {
                "crawl_result": failed_result,
                "scraped_pages": final_scraped_pages,
                "failed_pages": [],
                "homepage_scraped_page": homepage_scraped_page,
                "current_step": "website_crawling",
                "workflow_status": "running",
                "errors": existing_errors,
            }

        return {
            "crawl_result": failed_result,
            "scraped_pages": [],
            "failed_pages": [],
            "homepage_scraped_page": None,
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

    on_progress = state.get("on_progress")
    if on_progress:
        try:
            cand_count = len(state.get("candidate_urls") or [])
            sel_count = len(cleaned_selected_pages)
            msg = (
                f"Reading {sel_count} page(s) (found {cand_count} pages)..."
                if cand_count
                else f"Reading {sel_count} selected page(s)..."
            )
            res = on_progress("reading_pages", msg, {"page_count": sel_count, "found_pages": cand_count})
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            pass

    # 4. Check if existing homepage content is complete and reusable to avoid redundant crawling
    is_homepage_reusable = (
        homepage_scraped_page is not None
        and bool(homepage_scraped_page.content and homepage_scraped_page.content.strip())
        and len(homepage_scraped_page.content.strip()) >= MIN_PAGE_CONTENT_CHARS
    )

    pages_to_crawl: list[SelectedPage] = []
    homepage_reused = False

    if is_homepage_reusable:
        homepage_norm_urls: set[str] = set()
        if homepage_scraped_page and homepage_scraped_page.url:
            homepage_norm_urls.add(_normalize_url_for_merge(homepage_scraped_page.url))
        if clean_url:
            homepage_norm_urls.add(_normalize_url_for_merge(clean_url))

        for p in cleaned_selected_pages:
            if _normalize_url_for_merge(p.url) in homepage_norm_urls:
                homepage_reused = True
                logger.info(
                    "Reusing existing homepage content for '%s' (%d chars); excluding from Apify crawl.",
                    p.url,
                    len(homepage_scraped_page.content.strip()),  # type: ignore[union-attr]
                )
            else:
                pages_to_crawl.append(p)
    else:
        pages_to_crawl = list(cleaned_selected_pages)

    if homepage_reused and not pages_to_crawl:
        logger.info("All selected pages already available from homepage discovery; skipping Apify crawl.")
        crawl_result = WebsiteCrawlResult(
            website_url=clean_url or (homepage_scraped_page.url if homepage_scraped_page else "unknown"),
            status="success",
            scraped_pages=[],
            failed_pages=[],
            total_requested=len(cleaned_selected_pages),
            total_scraped=1,
            total_failed=0,
            has_usable_content=True,
            execution_time_seconds=0.0,
        )
    else:
        try:
            crawler_max_pages = (
                max(1, settings.MAX_PAGES_USER_SITE - 1)
                if is_homepage_reusable
                else settings.MAX_PAGES_USER_SITE
            )
            pages_to_crawl = pages_to_crawl[:crawler_max_pages]
            crawl_result = await crawl_selected_pages(
                pages=pages_to_crawl,
                website_url=clean_url,
                max_pages=crawler_max_pages,
            )
        except Exception as exc:
            logger.exception("Unexpected exception in website content crawler for '%s': %s", clean_url, exc)
            error_msg = f"Website crawling failed unexpectedly for '{clean_url}': {exc}"
            final_scraped_pages = _merge_homepage_into_scraped_pages(homepage_scraped_page, [])
            failed_result = WebsiteCrawlResult(
                website_url=clean_url or "unknown",
                status="failed",
                scraped_pages=final_scraped_pages,
                failed_pages=[
                    FailedPageScrape(url=p.url, error_message=error_msg, page_type=p.page_type)
                    for p in pages_to_crawl
                ],
                total_requested=len(cleaned_selected_pages),
                total_scraped=len(final_scraped_pages),
                total_failed=len(pages_to_crawl),
                has_usable_content=bool(final_scraped_pages),
                error_message=error_msg,
            )
            existing_errors = list(state.get("errors") or [])
            existing_errors.append(error_msg)

            if final_scraped_pages:
                logger.info(
                    "Preserving homepage (%d chars) despite crawling exception for '%s'.",
                    len(final_scraped_pages[0].content),
                    clean_url,
                )
                return {
                    "crawl_result": failed_result,
                    "scraped_pages": final_scraped_pages,
                    "failed_pages": failed_result.failed_pages,
                    "homepage_scraped_page": homepage_scraped_page,
                    "current_step": "website_crawling",
                    "workflow_status": "running",
                    "errors": existing_errors,
                }

            return {
                "crawl_result": failed_result,
                "scraped_pages": [],
                "failed_pages": failed_result.failed_pages,
                "homepage_scraped_page": None,
                "current_step": "website_crawling",
                "workflow_status": "failed",
                "error_message": error_msg,
                "errors": existing_errors,
            }

    # 5. Handle complete internal crawl failure (0 scraped pages or no usable content)
    merged_pages = _merge_homepage_into_scraped_pages(
        homepage_scraped_page,
        crawl_result.scraped_pages,
    )

    # Ensure unique website pages and cap at total limit (6 pages total)
    seen_urls: set[str] = set()
    deduped_final_pages: list[ScrapedPage] = []
    for p in merged_pages:
        norm_u = _normalize_url_for_merge(p.url)
        if norm_u not in seen_urls:
            seen_urls.add(norm_u)
            deduped_final_pages.append(p)
        if len(deduped_final_pages) >= settings.MAX_PAGES_USER_SITE:
            break
    final_scraped_pages = deduped_final_pages

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
        logger.warning("Website Crawling Node: internal crawl produced no usable content: %s", error_msg)
        existing_errors = list(state.get("errors") or [])
        existing_errors.append(error_msg)

        if final_scraped_pages:
            logger.info(
                "Preserving homepage (%d chars) despite complete internal crawling failure for '%s'.",
                len(final_scraped_pages[0].content),
                clean_url,
            )
            crawl_result.scraped_pages = final_scraped_pages
            crawl_result.has_usable_content = True
            crawl_result.total_scraped = len(final_scraped_pages)
            crawl_result.total_requested = len(cleaned_selected_pages)

            return {
                "crawl_result": crawl_result,
                "scraped_pages": final_scraped_pages,
                "failed_pages": crawl_result.failed_pages,
                "homepage_scraped_page": homepage_scraped_page,
                "current_step": "website_crawling",
                "workflow_status": "running",
                "errors": existing_errors,
            }

        return {
            "crawl_result": crawl_result,
            "scraped_pages": [],
            "failed_pages": crawl_result.failed_pages,
            "homepage_scraped_page": None,
            "current_step": "website_crawling",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": existing_errors,
        }

    # 6. Successful or partial crawl with usable content.
    crawl_result.scraped_pages = final_scraped_pages
    crawl_result.total_scraped = len(final_scraped_pages)
    if homepage_reused:
        crawl_result.total_requested = len(cleaned_selected_pages)
        if crawl_result.total_failed == 0 and crawl_result.status != "failed":
            crawl_result.status = "success"

    total_chars = sum(len(p.content) for p in final_scraped_pages)
    logger.info(
        "Website Crawling Node completed for '%s': status='%s', total_pages=%d/%d (homepage_reused=%s), failed=%d, total_content_chars=%d.",
        clean_url,
        crawl_result.status,
        len(final_scraped_pages),
        crawl_result.total_requested,
        "yes" if homepage_reused else "no",
        crawl_result.total_failed,
        total_chars,
    )

    crawl_result.scraped_pages = final_scraped_pages
    crawl_result.total_scraped = len(final_scraped_pages)

    update: dict[str, Any] = {
        "crawl_result": crawl_result,
        "scraped_pages": final_scraped_pages,
        "failed_pages": crawl_result.failed_pages,
        "homepage_scraped_page": homepage_scraped_page,
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

