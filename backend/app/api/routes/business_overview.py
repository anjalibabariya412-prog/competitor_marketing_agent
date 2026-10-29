"""FastAPI route for generating an evidence-based business overview via LangGraph.

Exposes a POST endpoint that validates business intake data, invokes the compiled
LangGraph workflow sequentially, and returns a structured business overview response.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from langgraph.graph.state import CompiledStateGraph

from backend.app.agent.graph import get_graph
from backend.app.agent.state import AgentState
from backend.app.schemas.business_form import BusinessForm
from backend.app.schemas.business_overview_response import (
    BusinessOverviewMetadata,
    BusinessOverviewResponse,
)
from backend.app.services.business_overview_service import (
    BusinessOverview,
    BusinessOverviewResult,
)
from backend.app.services.sitemap_fallback_service import WebsiteURLDiscoveryResult
from backend.app.services.website_content_crawler_service import WebsiteCrawlResult

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Business Overview"])


@router.post(
    "/api/v1/business-overview",
    response_model=BusinessOverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate Business Overview",
    description=(
        "Analyzes a business website through URL discovery, page selection, "
        "content crawling, and structured LLM synthesis to generate an evidence-based "
        "business overview with offerings, ICP, value propositions, and information gaps."
    ),
    responses={
        200: {
            "model": BusinessOverviewResponse,
            "description": "Business overview successfully generated.",
        },
        422: {
            "description": "Validation error in request body or insufficient readable website content.",
        },
        502: {
            "description": "Upstream failure crawling target website or external provider error.",
        },
        500: {
            "description": "Unexpected internal error during workflow execution.",
        },
    },
)
async def generate_business_overview_endpoint(
    business_form: BusinessForm,
    graph: CompiledStateGraph = Depends(get_graph),
) -> BusinessOverviewResponse:
    """Execute the Competitor Marketing Agent workflow and return the synthesized business overview."""
    target_url = str(business_form.business_website)
    logger.info("Received business overview request for website: '%s'", target_url)

    # 1. Prepare initial state with the validated business form
    initial_state: AgentState = {
        "business_form": business_form,
    }

    # 2. Asynchronously invoke the existing compiled LangGraph workflow
    try:
        final_state: dict[str, Any] = await graph.ainvoke(initial_state)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Unexpected exception while invoking LangGraph workflow for '%s': %s",
            target_url,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing the business overview request.",
        )

    # 3. Extract relevant state fields
    workflow_status = final_state.get("workflow_status")
    business_overview: BusinessOverview | None = final_state.get("business_overview")
    overview_result: BusinessOverviewResult | None = final_state.get("overview_result")
    crawl_result: WebsiteCrawlResult | None = final_state.get("crawl_result")
    discovery_result: WebsiteURLDiscoveryResult | None = final_state.get("discovery_result")
    error_message: str | None = final_state.get("error_message")

    # 4. Handle upstream website crawling or URL discovery failures (WAF block, connectivity, etc.)
    if (
        (crawl_result is not None and crawl_result.status == "failed" and not crawl_result.has_usable_content)
        or (
            discovery_result is not None
            and discovery_result.homepage_status == "failed"
            and discovery_result.sitemap_status in ("failed", "no_sitemap_found")
        )
        or (error_message and ("blocked by target server" in error_message.lower() or "waf" in error_message.lower()))
    ):
        err_detail = (
            (crawl_result.error_message if crawl_result and crawl_result.error_message else None)
            or error_message
            or "Failed to crawl or discover content from the target website."
        )
        logger.warning(
            "Upstream crawling or discovery failure for '%s': %s",
            target_url,
            err_detail,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=err_detail,
        )

    # 5. Handle insufficient readable website content (< 150 characters or empty pages)
    if overview_result is not None and overview_result.status == "insufficient_content":
        err_detail = (
            overview_result.error_message
            or "Target website does not contain sufficient readable content to generate a business overview."
        )
        logger.warning(
            "Business overview request failed due to insufficient content for '%s': %s",
            target_url,
            err_detail,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=err_detail,
        )

    # 6. Handle general workflow execution failure
    if workflow_status != "completed" or business_overview is None:
        err_detail = error_message or "Business overview workflow could not be completed."
        logger.error(
            "Workflow did not complete successfully for '%s' (status=%s): %s",
            target_url,
            workflow_status,
            err_detail,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=err_detail,
        )

    # 7. Construct response metadata
    metadata = BusinessOverviewMetadata(
        website_url=target_url,
        total_candidates_discovered=len(final_state.get("candidate_urls") or []),
        total_pages_selected=len(final_state.get("selected_pages") or []),
        total_pages_analyzed=(
            overview_result.total_pages_analyzed
            if overview_result is not None
            else len(final_state.get("scraped_pages") or [])
        ),
        total_findings=(
            overview_result.total_findings
            if overview_result is not None
            else len(business_overview.key_findings)
        ),
        model_used=overview_result.model_used if overview_result is not None else None,
        execution_time_seconds=(
            overview_result.execution_time_seconds
            if overview_result is not None
            else 0.0
        ),
    )

    logger.info(
        "Successfully generated business overview for '%s': %d products, %d findings.",
        target_url,
        len(business_overview.products_and_services),
        metadata.total_findings,
    )

    # 8. Return structured response
    return BusinessOverviewResponse(
        status="success",
        business_overview=business_overview,
        website_url=target_url,
        execution_time_seconds=metadata.execution_time_seconds,
    )
