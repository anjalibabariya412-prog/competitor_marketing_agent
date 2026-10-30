"""LangGraph URL Discovery Node for the Competitor Marketing Agent.

This node is the first operational stage in the pipeline after initial business intake.
It extracts the business website URL from the shared `AgentState`, triggers the
existing URL discovery service (which orchestrates homepage fetching, candidate URL
extraction, and conditional sitemap fallback), and stores the results back into
`AgentState`.

Phase 1 addition: After URL discovery, the homepage HTML (already fetched internally
by the discovery service) is converted into a ``ScrapedPage`` and stored in
``homepage_scraped_page`` so that the Website Crawling node can merge it into the
final page set without issuing a second network request.
"""

from __future__ import annotations

import logging
from typing import Any

from ..state import AgentState
from backend.app.schemas.business_form import BusinessForm
from backend.app.schemas.enums import PageType
from backend.app.schemas.scraped_data import ScrapedPage
from backend.app.services.homepage_service import HomepageFetchResult
from backend.app.services.sitemap_fallback_service import (
    WebsiteURLDiscoveryResult,
    discover_website_urls,
)
from backend.app.services.url_extractor_service import CandidateURL
from bs4 import BeautifulSoup
from backend.app.services.website_content_crawler_service import _extract_text_from_html

logger = logging.getLogger(__name__)


def _homepage_html_to_scraped_page(
    homepage_fetch_result: HomepageFetchResult,
) -> ScrapedPage | None:
    """Convert a successful ``HomepageFetchResult`` into a ``ScrapedPage``.

    Returns ``None`` when:
    - The fetch status is not ``"success"``.
    - The HTML is missing or empty.
    - The extracted text is genuinely empty after stripping whitespace.

    All non-empty extracted text is preserved in full — no minimum-length
    threshold is applied and no upper character limit is enforced.
    """
    if homepage_fetch_result.status != "success":
        return None

    html = homepage_fetch_result.html or ""
    if not html.strip():
        return None

    text = _extract_text_from_html(html).strip()

    if not text:
        return None

    page_url = homepage_fetch_result.final_url or homepage_fetch_result.requested_url

    extracted_title: str | None = None
    try:
        soup = BeautifulSoup(html, "html.parser")
        if soup.title:
            raw_title = soup.title.get_text()
            normalized_title = " ".join(raw_title.split())
            if normalized_title:
                extracted_title = normalized_title
    except Exception:
        extracted_title = None

    return ScrapedPage(
        url=page_url,
        title=extracted_title,
        content=text,
        page_type=PageType.HOME,
        status_code=homepage_fetch_result.http_status_code,
        metadata={},
    )


async def url_discovery_node(state: AgentState) -> dict[str, Any]:
    """Execute the URL Discovery stage of the Competitor Marketing Agent workflow.

    Reads the business website URL from `state["business_form"].business_website`,
    invokes the integrated discovery pipeline (`discover_website_urls`, which executes
    homepage fetching, link extraction, and conditional sitemap fallback), and returns
    a partial state update with the discovery metadata and candidate URLs.

    Args:
        state: Shared LangGraph `AgentState` containing at minimum `business_form`.

    Returns:
        Partial state dictionary with updated fields:
        - `discovery_result`: Full `WebsiteURLDiscoveryResult` metadata.
        - `candidate_urls`: Discovered `list[CandidateURL]` items.
        - `current_step`: "url_discovery".
        - `workflow_status`: "running" on success, or "failed" if no candidate URLs
          were discovered or an unrecoverable failure occurred.
        - `error_message`: Human-readable error description (on failure only).
        - `errors`: Cumulative list of error messages (on failure only).
    """
    logger.info("Entering URL Discovery Node.")

    # 1. Validate state input structure
    if not isinstance(state, dict):
        error_msg = f"URL Discovery Node expected dict/AgentState, received {type(state).__name__}."
        logger.error(error_msg)
        return {
            "current_step": "url_discovery",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": [error_msg],
        }

    # 2. Extract business_website and product_or_service from business_form
    business_form = state.get("business_form")
    website_url: str | None = None
    product_or_service: str | None = None

    if isinstance(business_form, BusinessForm):
        website_url = business_form.business_website
        product_or_service = business_form.product_or_service
    elif isinstance(business_form, dict):
        website_url = business_form.get("business_website")
        product_or_service = business_form.get("product_or_service")

    if not website_url or not isinstance(website_url, str) or not website_url.strip():
        error_msg = (
            "URL Discovery Node failed: 'business_form' is missing or does not contain a "
            "valid 'business_website' URL."
        )
        logger.error(error_msg)
        failed_result = WebsiteURLDiscoveryResult(
            requested_url="",
            base_url="",
            status="failed",
            homepage_status="failed",
            candidate_urls=[],
            total_candidates=0,
        )
        existing_errors = list(state.get("errors") or [])
        existing_errors.append(error_msg)
        return {
            "discovery_result": failed_result,
            "candidate_urls": [],
            "current_step": "url_discovery",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": existing_errors,
        }

    clean_url = website_url.strip()
    logger.info("URL Discovery Node processing business website: %s", clean_url)

    # 3. Call existing URL discovery orchestrator
    try:
        discovery_result = await discover_website_urls(clean_url, product_or_service=product_or_service)
    except Exception as exc:
        logger.exception("Unexpected exception during URL discovery for '%s': %s", clean_url, exc)
        error_msg = f"URL discovery failed unexpectedly for '{clean_url}': {exc}"
        failed_result = WebsiteURLDiscoveryResult(
            requested_url=clean_url,
            base_url=clean_url,
            status="failed",
            homepage_status="failed",
            candidate_urls=[],
            total_candidates=0,
        )
        existing_errors = list(state.get("errors") or [])
        existing_errors.append(error_msg)
        return {
            "discovery_result": failed_result,
            "candidate_urls": [],
            "homepage_scraped_page": None,
            "current_step": "url_discovery",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": existing_errors,
        }

    # Convert homepage HTML (fetched inside discover_website_urls) into a ScrapedPage
    # regardless of whether any internal candidate URLs were discovered.
    homepage_scraped_page: ScrapedPage | None = None
    hp_fetch_result = getattr(discovery_result, "homepage_fetch_result", None)
    if hp_fetch_result is not None:
        try:
            homepage_scraped_page = _homepage_html_to_scraped_page(hp_fetch_result)
            if homepage_scraped_page:
                logger.info(
                    "Homepage converted to ScrapedPage (%d chars) for '%s' (page_type=%s).",
                    len(homepage_scraped_page.content),
                    homepage_scraped_page.url,
                    homepage_scraped_page.page_type,
                )
            else:
                logger.debug(
                    "Homepage HTML for '%s' could not be converted to a usable ScrapedPage "
                    "(status='%s', html present=%s).",
                    clean_url,
                    getattr(hp_fetch_result, "status", "unknown"),
                    bool(getattr(hp_fetch_result, "html", None)),
                )
        except Exception as exc:
            logger.warning(
                "Non-fatal: failed to convert homepage HTML to ScrapedPage for '%s': %s",
                clean_url,
                exc,
            )
            homepage_scraped_page = None

    # 4. Check for discovery failure or 0 discovered candidates
    if discovery_result.status == "failed" or not discovery_result.candidate_urls:
        error_msg = (
            f"URL discovery yielded 0 candidate URLs for '{clean_url}' "
            f"(homepage status: '{discovery_result.homepage_status}', "
            f"sitemap status: '{discovery_result.sitemap_status}')."
        )
        logger.warning("URL Discovery Node failure: %s", error_msg)
        existing_errors = list(state.get("errors") or [])
        existing_errors.append(error_msg)
        return {
            "discovery_result": discovery_result,
            "candidate_urls": [],
            "homepage_scraped_page": homepage_scraped_page,
            "current_step": "url_discovery",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": existing_errors,
        }

    # 5. Successful or partial discovery with usable candidate URLs
    logger.info(
        "URL Discovery Node completed successfully for '%s': %d candidate URLs discovered "
        "(status: '%s', homepage: '%s', sitemap triggered: %s).",
        clean_url,
        discovery_result.total_candidates,
        discovery_result.status,
        discovery_result.homepage_status,
        discovery_result.sitemap_triggered,
    )

    return {
        "discovery_result": discovery_result,
        "candidate_urls": discovery_result.candidate_urls,
        "homepage_scraped_page": homepage_scraped_page,
        "current_step": "url_discovery",
        "workflow_status": "running",
    }

