"""FastAPI route for generating an evidence-based business overview via LangGraph.

Exposes a POST endpoint that validates business intake data, invokes the compiled
LangGraph workflow, and streams real-time progress events and the final synthesized
Business Overview via Server-Sent Events (SSE).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langgraph.graph.state import CompiledStateGraph

from backend.app.agent.graph import get_graph
from backend.app.agent.state import AgentState
from backend.app.db import get_db_context
from backend.app.schemas.business_form import BusinessForm
from backend.app.schemas.business_overview_response import (
    BusinessOverviewMetadata,
    BusinessOverviewResponse,
)
from backend.app.services.business_overview_service import (
    BusinessOverview,
    BusinessOverviewResult,
    save_business_overview,
)
from backend.app.services.sitemap_fallback_service import WebsiteURLDiscoveryResult
from backend.app.services.website_content_crawler_service import WebsiteCrawlResult

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Business Overview"])


@router.post(
    "/api/v1/business-overview",
    status_code=status.HTTP_200_OK,
    summary="Generate Business Overview (SSE Stream)",
    description=(
        "Analyzes a business website through URL discovery, page selection, "
        "content crawling, and structured LLM synthesis to generate an evidence-based "
        "business overview. Streams real-time progress events via Server-Sent Events (SSE) "
        "and emits a final 'done' event with the complete BusinessOverviewResponse payload."
    ),
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "Server-Sent Events (SSE) stream yielding real-time progress events "
                "(stages: visiting_website, reading_pages, extracting_info, identifying_audience, "
                "writing_overview) and completing with a 'done' event containing the structured "
                "BusinessOverviewResponse payload (or 'scrape_failed' / 'error' events on failure)."
            ),
            "content": {
                "text/event-stream": {
                    "example": "data: {\"stage\": \"done\", \"message\": \"Business overview successfully generated.\", \"data\": {\"status\": \"success\", \"business_overview\": {...}}, \"meta\": {...}}\n\n"
                }
            },
        },
        422: {
            "description": "Validation error in request body.",
        },
    },
)
async def generate_business_overview_endpoint(
    business_form: BusinessForm,
    request: Request,
    graph: CompiledStateGraph = Depends(get_graph),
) -> StreamingResponse:
    """Stream real-time stage progress events and final Business Overview payload via SSE."""
    target_url = str(business_form.business_website)
    logger.info("Received streaming business overview request for website: '%s'", target_url)

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def on_progress(stage: str, message: str, meta: dict[str, Any] | None = None) -> None:
        await queue.put({
            "stage": stage,
            "message": message,
            "meta": meta or {},
        })

    initial_state: AgentState = {
        "business_form": business_form,
        "on_progress": on_progress,
    }

    t0_pipeline = time.perf_counter()
    pipeline_outcome = "failed"

    async def _timed_ainvoke() -> dict[str, Any]:
        nonlocal pipeline_outcome
        try:
            result = await graph.ainvoke(initial_state)
            if isinstance(result, dict):
                status_val = result.get("workflow_status")
                if status_val == "completed":
                    pipeline_outcome = "completed"
                elif status_val == "failed":
                    pipeline_outcome = "failed"
                else:
                    pipeline_outcome = status_val or "completed"
            else:
                pipeline_outcome = "completed"
            return result
        except asyncio.CancelledError:
            pipeline_outcome = "cancelled"
            raise
        except Exception:
            pipeline_outcome = "exception"
            raise
        finally:
            pipeline_duration = round(time.perf_counter() - t0_pipeline, 2)
            logger.info(
                "[PIPELINE_TIMING] stage=total_pipeline duration_seconds=%.2f outcome=%s website='%s'",
                pipeline_duration,
                pipeline_outcome,
                target_url,
            )

    # Run the workflow in a background asyncio task
    workflow_task = asyncio.create_task(_timed_ainvoke())

    async def event_generator():
        try:
            # Poll events until workflow task finishes
            while not workflow_task.done():
                if await request.is_disconnected():
                    logger.info("Client disconnected from SSE stream for '%s'; cancelling workflow task.", target_url)
                    workflow_task.cancel()
                    return

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.3)
                    if event is not None:
                        yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    continue

            # Drain any remaining progress events queued before task finished
            while not queue.empty():
                event = queue.get_nowait()
                if event is not None:
                    yield f"data: {json.dumps(event)}\n\n"

            if workflow_task.cancelled():
                logger.info("Workflow task for '%s' was cancelled.", target_url)
                return

            task_exc = workflow_task.exception()
            if task_exc:
                logger.exception("Workflow task encountered unexpected exception for '%s': %s", target_url, task_exc)
                err_payload = {
                    "stage": "error",
                    "message": "An unexpected error occurred while processing the business overview request.",
                    "meta": {"error_type": "internal"},
                }
                yield f"data: {json.dumps(err_payload)}\n\n"
                return

            final_state = workflow_task.result()

            workflow_status = final_state.get("workflow_status")
            business_overview: BusinessOverview | None = final_state.get("business_overview")
            overview_result: BusinessOverviewResult | None = final_state.get("overview_result")
            crawl_result: WebsiteCrawlResult | None = final_state.get("crawl_result")
            discovery_result: WebsiteURLDiscoveryResult | None = final_state.get("discovery_result")
            error_message: str | None = final_state.get("error_message")

            # Check upstream scraping failure (blocked, timeout, no usable content)
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
                err_payload = {
                    "stage": "scrape_failed",
                    "message": err_detail,
                    "meta": {"error_type": "scrape_failed", "can_retry": True},
                }
                yield f"data: {json.dumps(err_payload)}\n\n"
                return

            # Check insufficient readable content
            if overview_result is not None and overview_result.status == "insufficient_content":
                err_detail = (
                    overview_result.error_message
                    or "Target website does not contain sufficient readable content to generate a business overview."
                )
                err_payload = {
                    "stage": "error",
                    "message": err_detail,
                    "meta": {"error_type": "insufficient_content"},
                }
                yield f"data: {json.dumps(err_payload)}\n\n"
                return

            # General failure
            if workflow_status != "completed" or business_overview is None:
                err_detail = error_message or "Business overview workflow could not be completed."
                err_payload = {
                    "stage": "error",
                    "message": err_detail,
                    "meta": {"error_type": "workflow_incomplete"},
                }
                yield f"data: {json.dumps(err_payload)}\n\n"
                return

            # Success: Persist business overview to database (creates a new immutable record)
            try:
                with get_db_context() as db:
                    save_business_overview(db, business_overview)
            except Exception as exc:
                logger.exception("Failed to persist business overview during stream for '%s': %s", target_url, exc)
                err_payload = {
                    "stage": "error",
                    "message": "Failed to save business overview to database.",
                    "meta": {"error_type": "database_persistence_error"},
                }
                yield f"data: {json.dumps(err_payload)}\n\n"
                return

            # Build response payload
            metadata = BusinessOverviewMetadata(
                website_url=target_url,
                total_candidates_discovered=len(final_state.get("candidate_urls") or []),
                total_pages_selected=len(final_state.get("selected_pages") or []),
                total_pages_analyzed=(
                    overview_result.total_pages_analyzed
                    if overview_result is not None
                    else len(final_state.get("scraped_pages") or [])
                ),
                total_findings=overview_result.total_findings if overview_result is not None else 0,
                model_used=overview_result.model_used if overview_result is not None else None,
                execution_time_seconds=(
                    overview_result.execution_time_seconds
                    if overview_result is not None
                    else 0.0
                ),
            )

            response_data = BusinessOverviewResponse(
                status="success",
                business_overview=business_overview,
                website_url=target_url,
                total_pages_analyzed=metadata.total_pages_analyzed,
                execution_time_seconds=metadata.execution_time_seconds,
            )

            done_payload = {
                "stage": "done",
                "message": "Business overview successfully generated.",
                "data": response_data.model_dump(),
                "meta": {
                    "total_pages_analyzed": metadata.total_pages_analyzed,
                    "execution_time_seconds": metadata.execution_time_seconds,
                },
            }
            yield f"data: {json.dumps(done_payload)}\n\n"

        except asyncio.CancelledError:
            logger.info("SSE event stream cancelled for '%s'.", target_url)
            if not workflow_task.done():
                workflow_task.cancel()
            raise
        finally:
            if not workflow_task.done():
                workflow_task.cancel()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
