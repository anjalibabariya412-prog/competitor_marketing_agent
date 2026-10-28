"""Pydantic response schema for the Business Overview API endpoint."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from backend.app.services.business_overview_service import BusinessOverview


class BusinessOverviewMetadata(BaseModel):
    """Internal execution statistics and pipeline metadata for the business overview workflow."""

    model_config = ConfigDict(str_strip_whitespace=True)

    website_url: str = Field(description="Primary business website URL analyzed.")
    total_candidates_discovered: int = Field(
        default=0,
        description="Total candidate URLs discovered during URL discovery.",
    )
    total_pages_selected: int = Field(
        default=0,
        description="Total candidate URLs selected for deep crawling.",
    )
    total_pages_analyzed: int = Field(
        default=0,
        description="Total scraped pages with readable text submitted to LLM synthesis.",
    )
    total_findings: int = Field(
        default=0,
        description="Total structured evidence findings extracted from the pages.",
    )
    model_used: str | None = Field(
        default=None,
        description="Identifier of the LLM model used for synthesis.",
    )
    execution_time_seconds: float = Field(
        default=0.0,
        description="End-to-end execution duration in seconds.",
    )


class BusinessOverviewResponse(BaseModel):
    """Standardized response schema returned by the Business Overview API endpoint."""

    model_config = ConfigDict(str_strip_whitespace=True)

    status: str = Field(
        default="success",
        description="Outcome of the overview generation request ('success').",
    )
    business_overview: BusinessOverview = Field(
        description="Synthesized evidence-based business overview.",
    )
    website_url: str = Field(
        description="Primary business website URL analyzed.",
    )
    execution_time_seconds: float = Field(
        default=0.0,
        description="Duration of overview generation in seconds.",
    )
