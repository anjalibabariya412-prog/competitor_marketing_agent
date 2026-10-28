"""LangGraph URL Discovery Node for the Competitor Marketing Agent.

This node is the first operational stage in the pipeline after initial business intake.
It extracts the business website URL from the shared `AgentState`, triggers the
existing URL discovery service (which orchestrates homepage fetching, candidate URL
extraction, and conditional sitemap fallback), and stores the results back into
`AgentState`.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from ..state import AgentState
    from ...schemas.business_form import BusinessForm
    from ...services.sitemap_fallback_service import (
        WebsiteURLDiscoveryResult,
        discover_website_urls,
    )
    from ...services.url_extractor_service import CandidateURL
except (ImportError, ValueError):
    try:
        from app.agent.state import AgentState
        from app.schemas.business_form import BusinessForm
        from app.services.sitemap_fallback_service import (
            WebsiteURLDiscoveryResult,
            discover_website_urls,
        )
        from app.services.url_extractor_service import CandidateURL
    except ModuleNotFoundError:
        from backend.app.agent.state import AgentState
        from backend.app.schemas.business_form import BusinessForm
        from backend.app.services.sitemap_fallback_service import (
            WebsiteURLDiscoveryResult,
            discover_website_urls,
        )
        from backend.app.services.url_extractor_service import CandidateURL

logger = logging.getLogger(__name__)


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

    # 2. Extract business_website from business_form
    business_form = state.get("business_form")
    website_url: str | None = None

    if isinstance(business_form, BusinessForm):
        website_url = business_form.business_website
    elif isinstance(business_form, dict):
        website_url = business_form.get("business_website")

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
        discovery_result = await discover_website_urls(clean_url)
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
            "current_step": "url_discovery",
            "workflow_status": "failed",
            "error_message": error_msg,
            "errors": existing_errors,
        }

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
        "current_step": "url_discovery",
        "workflow_status": "running",
    }

