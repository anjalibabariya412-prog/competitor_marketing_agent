"""LangGraph Page Selection Node for the Competitor Marketing Agent.

This node is the second operational stage in the workflow pipeline.
It consumes candidate URLs discovered during the URL discovery phase,
calls the page selection service (which utilizes structured LLM selection
via gpt-4o-mini with a robust deterministic priority-based fallback),
and populates `selection_result` and `selected_pages` in `AgentState`.
"""

from __future__ import annotations

import logging
import time
from typing import Any
import urllib.parse

from ..state import AgentState
from backend.app.core.config import settings
from backend.app.schemas.business_form import BusinessForm
from backend.app.schemas.enums import PageType
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.content_validation import is_content_adequate
from backend.app.services.page_selection_service import (
    PageSelectionResult,
    SelectedPage,
    SelectionCategory,
    categorize_candidate,
    select_pages_with_llm,
)
from backend.app.services.sitemap_fallback_service import WebsiteURLDiscoveryResult
from backend.app.services.url_extractor_service import CandidateURL
from backend.app.services.website_content_crawler_service import MIN_PAGE_CONTENT_CHARS

logger = logging.getLogger(__name__)


def _normalize_url_for_merge(url: str) -> str:
    """Normalize URL for deduplication and comparison with homepage."""
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


async def page_selection_node(state: AgentState) -> dict[str, Any]:
    """Execute the Page Selection stage with [PIPELINE_TIMING] measurement."""
    logger.info("Entering Page Selection Node.")

    t0 = time.perf_counter()
    status_str = "failed"
    target_site = ""
    try:
        business_form = state.get("business_form") if isinstance(state, dict) else None
        if isinstance(business_form, BusinessForm):
            target_site = business_form.business_website
        elif isinstance(business_form, dict):
            target_site = business_form.get("business_website", "")

        result = await _page_selection_node_impl(state)
        status_str = "success" if result.get("workflow_status") == "running" else "failed"
        return result
    except Exception:
        status_str = "exception"
        raise
    finally:
        duration = round(time.perf_counter() - t0, 2)
        logger.info(
            "[PIPELINE_TIMING] stage=page_selection duration_seconds=%.2f status=%s website='%s'",
            duration,
            status_str,
            target_site,
        )


async def _page_selection_node_impl(state: AgentState) -> dict[str, Any]:
    """Execute the Page Selection stage of the Competitor Marketing Agent workflow.

    Reads `candidate_urls` from `state`, extracts the target `website_url` from
    `discovery_result` or `business_form`, and calls `select_pages_with_llm` to select
    the most relevant pages for competitive intelligence.

    Args:
        state: Shared LangGraph `AgentState` containing at minimum `candidate_urls`.

    Returns:
        Partial state dictionary with updated fields:
        - `selection_result`: Full `PageSelectionResult` instance.
        - `selected_pages`: `list[SelectedPage]` prioritized pages for crawling.
        - `current_step`: "page_selection".
        - `workflow_status`: "running" on success, or "failed" if no pages selected or on fatal error.
        - `error_message`: Set on failure.
        - `errors`: Cumulative list of errors/advisories if any occurred.
    """


    # 1. Validate state input structure
    if not isinstance(state, dict):
        error_msg = f"Page Selection Node expected dict/AgentState, received {type(state).__name__}."
        logger.error(error_msg)
        return {
            "current_step": "page_selection",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": [error_msg],
        }

    # 2. Extract and validate candidate_urls from state
    raw_candidates = state.get("candidate_urls")
    cleaned_candidates: list[CandidateURL] = []

    if isinstance(raw_candidates, list):
        for item in raw_candidates:
            if isinstance(item, CandidateURL):
                cleaned_candidates.append(item)
            elif isinstance(item, dict):
                try:
                    cleaned_candidates.append(CandidateURL(**item))
                except Exception:
                    continue

    if not cleaned_candidates:
        error_msg = (
            "Page Selection Node failed: 'candidate_urls' is missing or empty in AgentState. "
            "No candidate pages available for selection."
        )
        logger.warning(error_msg)
        failed_result = PageSelectionResult(
            selected_pages=[],
            total_selected=0,
            selection_method="empty",
            error_message="Candidate URL list is empty; no pages to select.",
        )
        existing_errors = list(state.get("errors") or [])
        existing_errors.append(error_msg)
        return {
            "selection_result": failed_result,
            "selected_pages": [],
            "current_step": "page_selection",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": existing_errors,
        }

    # 3. Resolve target website_url for the service
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

    if not website_url and cleaned_candidates:
        parsed = urllib.parse.urlsplit(cleaned_candidates[0].url)
        if parsed.scheme and parsed.netloc:
            website_url = f"{parsed.scheme}://{parsed.netloc}"

    clean_url = website_url.strip() if website_url else ""
    logger.info(
        "Page Selection Node processing %d candidate URLs for website: '%s'.",
        len(cleaned_candidates),
        clean_url,
    )

    # 4. Extract product_or_service from business_form and invoke page selection
    business_form = state.get("business_form")
    product_or_service: str | None = None
    if isinstance(business_form, BusinessForm):
        product_or_service = business_form.product_or_service
    elif isinstance(business_form, dict):
        product_or_service = business_form.get("product_or_service")

    # 4. Check if homepage content is already available, valid, and adequate
    homepage_scraped_page: ScrapedPage | None = state.get("homepage_scraped_page")  # type: ignore[assignment]
    is_homepage_reusable = False
    if homepage_scraped_page is not None and bool(homepage_scraped_page.content and homepage_scraped_page.content.strip()):
        is_adeq, adeq_reason = is_content_adequate(
            homepage_scraped_page.content,
            url=homepage_scraped_page.url or clean_url,
            page_type=PageType.HOME,
            min_chars=settings.MIN_HOMEPAGE_ADEQUACY_CHARS,
        )
        is_homepage_reusable = is_adeq
        if not is_adeq:
            logger.warning(
                "Homepage content in state is inadequate (%s, %d chars). "
                "Will allow up to %d pages to be selected/crawled.",
                adeq_reason,
                len(homepage_scraped_page.content.strip()),
                settings.MAX_PAGES_USER_SITE,
            )

    try:
        if is_homepage_reusable:
            # Homepage counts as 1 of the 6 total pages.
            # Select up to 5 related pages from discovered URLs, excluding the homepage.
            max_related_pages = min(5, max(1, settings.MAX_PAGES_USER_SITE - 1))
            homepage_norm_urls: set[str] = set()
            if homepage_scraped_page and homepage_scraped_page.url:
                homepage_norm_urls.add(_normalize_url_for_merge(homepage_scraped_page.url))
            if clean_url:
                homepage_norm_urls.add(_normalize_url_for_merge(clean_url))
            if discovery_result is not None:
                for u in [getattr(discovery_result, "requested_url", None), getattr(discovery_result, "base_url", None)]:
                    if u:
                        homepage_norm_urls.add(_normalize_url_for_merge(u))

            related_candidates = [
                c for c in cleaned_candidates
                if _normalize_url_for_merge(c.url) not in homepage_norm_urls
                and categorize_candidate(c, base_url=clean_url) != SelectionCategory.HOME
            ]

            selection_result = await select_pages_with_llm(
                candidates=related_candidates,
                website_url=clean_url,
                product_or_service=product_or_service,
                max_pages=max_related_pages,
                exclude_homepage=True,
            )

            # Ensure no homepage URL is included and limit does not exceed 5
            filtered_selected = [
                p for p in selection_result.selected_pages
                if _normalize_url_for_merge(p.url) not in homepage_norm_urls
            ][:max_related_pages]
            selection_result.selected_pages = filtered_selected
            selection_result.total_selected = len(filtered_selected)
        else:
            # Homepage not available upfront; select up to total page limit (6 pages total)
            selection_result = await select_pages_with_llm(
                candidates=cleaned_candidates,
                website_url=clean_url,
                product_or_service=product_or_service,
                max_pages=settings.MAX_PAGES_USER_SITE,
            )
            selection_result.selected_pages = selection_result.selected_pages[:settings.MAX_PAGES_USER_SITE]
            selection_result.total_selected = len(selection_result.selected_pages)
    except Exception as exc:
        logger.exception("Unexpected exception in page selection service for '%s': %s", clean_url, exc)
        error_msg = f"Page selection service failed unexpectedly for '{clean_url}': {exc}"
        failed_result = PageSelectionResult(
            selected_pages=[],
            total_selected=0,
            selection_method="empty",
            error_message=error_msg,
        )
        existing_errors = list(state.get("errors") or [])
        existing_errors.append(error_msg)
        return {
            "selection_result": failed_result,
            "selected_pages": [],
            "current_step": "page_selection",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": existing_errors,
        }

    # 5. Handle selection failure (0 selected pages)
    if not selection_result.selected_pages or selection_result.total_selected == 0:
        if is_homepage_reusable:
            logger.info(
                "Page selection yielded 0 related pages for '%s', but homepage is already available; proceeding with homepage only.",
                clean_url,
            )
            return {
                "selection_result": selection_result,
                "selected_pages": [],
                "current_step": "page_selection",
                "workflow_status": "running",
            }

        error_msg = (
            f"Page selection yielded 0 selected pages for '{clean_url}': "
            f"{selection_result.error_message or 'No relevant candidate URLs were selected.'}"
        )
        logger.warning("Page Selection Node failure: %s", error_msg)
        existing_errors = list(state.get("errors") or [])
        existing_errors.append(error_msg)
        return {
            "selection_result": selection_result,
            "selected_pages": [],
            "current_step": "page_selection",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": existing_errors,
        }

    # 6. Successful selection
    logger.info(
        "Page Selection Node completed successfully for '%s': %d page(s) selected via '%s'.",
        clean_url,
        selection_result.total_selected,
        selection_result.selection_method,
    )

    update: dict[str, Any] = {
        "selection_result": selection_result,
        "selected_pages": selection_result.selected_pages,
        "current_step": "page_selection",
        "workflow_status": "running",
    }
    if selection_result.error_message:
        existing_errors = list(state.get("errors") or [])
        existing_errors.append(f"Page selection advisory: {selection_result.error_message}")
        update["errors"] = existing_errors

    return update

