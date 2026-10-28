"""Pydantic schemas for scraped web pages, social links, and extracted findings."""

from __future__ import annotations

from typing import Any
import urllib.parse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import FindingType, PageType, Platform, SocialSource


def _validate_http_url(url: str, field_name: str) -> str:
    """Validate that the provided string is a non-empty HTTP or HTTPS URL."""
    cleaned = url.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty.")
    parsed = urllib.parse.urlsplit(cleaned)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"{field_name} must use an http or https scheme.")
    if not parsed.netloc:
        raise ValueError(f"{field_name} must include a valid host or domain name.")
    return cleaned


class ScrapedPage(BaseModel):
    """Represents a single webpage retrieved from a website crawler."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_default=True,
    )

    url: str = Field(..., description="The page URL.")
    title: str | None = Field(default=None, description="Optional page title.")
    content: str = Field(..., description="Extracted text or Markdown content.")
    page_type: PageType = Field(
        default=PageType.UNKNOWN,
        description="Classified page type category.",
    )
    status_code: int | None = Field(
        default=None,
        description="Optional HTTP response status code.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional crawler metadata or headers.",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        return _validate_http_url(v, "Page URL")

    @field_validator("status_code")
    @classmethod
    def validate_status_code(cls, v: int | None) -> int | None:
        if v is not None and not (100 <= v <= 599):
            raise ValueError("status_code must be a valid HTTP status code between 100 and 599.")
        return v


class SocialLink(BaseModel):
    """Represents a social media profile link associated with a business or competitor."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_default=True,
    )

    url: str = Field(..., description="Social profile URL.")
    platform: Platform = Field(..., description="Social media or web platform.")
    source: SocialSource = Field(..., description="Discovery source of the link.")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        return _validate_http_url(v, "Social profile URL")


class Finding(BaseModel):
    """Represents an evidence-based factual statement extracted from website or social content."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_default=True,
    )

    statement: str = Field(..., description="Factual finding text or observation.")
    finding_type: FindingType = Field(..., description="Category classification of the finding.")
    source_url: str = Field(..., description="Source URL providing evidence for the finding.")
    is_inferred: bool = Field(
        default=False,
        description="Whether the finding is an analytical inference rather than explicitly stated.",
    )
    has_conflict: bool = Field(
        default=False,
        description="Whether contradictory or conflicting evidence was detected.",
    )

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Finding statement cannot be empty.")
        return v

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, v: str) -> str:
        return _validate_http_url(v, "Source URL")
