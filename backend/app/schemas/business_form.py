"""Pydantic model for validating the initial business intake form."""

from __future__ import annotations

import ipaddress
import re
from typing import Self
import urllib.parse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# Regex pattern matching valid host domains with a dot extension and no invalid symbols.
_DOMAIN_PATTERN = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z0-9-]{2,}$"
)


def _normalize_and_validate_web_url(url_str: str, field_label: str) -> str:
    """Normalize and validate a standard HTTP/HTTPS web URL.

    Prepends 'https://' if scheme is omitted, rejects localhost and bare IPs,
    and requires a valid domain containing a dot.
    """
    clean_url = url_str.strip()
    if not clean_url:
        raise ValueError(f"{field_label} cannot be empty.")

    if clean_url.startswith("//"):
        clean_url = f"https:{clean_url}"
    elif "://" not in clean_url:
        clean_url = f"https://{clean_url}"

    parsed = urllib.parse.urlsplit(clean_url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"{field_label} must use an http or https scheme.")

    raw_host = parsed.hostname
    if not raw_host:
        raise ValueError(f"{field_label} must contain a valid domain name.")

    hostname = raw_host.lower().rstrip(".")
    if not hostname:
        raise ValueError(f"{field_label} must contain a valid domain name.")

    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError(
            f"Localhost URLs are not allowed for {field_label.lower()}. Please provide a public domain."
        )

    try:
        ipaddress.ip_address(hostname)
        raise ValueError(
            f"Bare IP addresses are not allowed for {field_label.lower()}. Please provide a valid domain name."
        )
    except ValueError as exc:
        if "Bare IP addresses are not allowed" in str(exc):
            raise

    if "." not in hostname or not _DOMAIN_PATTERN.match(hostname):
        raise ValueError(
            f"{field_label} must contain a valid domain with a dot (e.g., 'example.com')."
        )

    # Omit default ports for canonical representation
    port_part = ""
    if parsed.port and not (
        (scheme == "http" and parsed.port == 80)
        or (scheme == "https" and parsed.port == 443)
    ):
        port_part = f":{parsed.port}"

    netloc = f"{hostname}{port_part}"
    return urllib.parse.urlunsplit(
        (scheme, netloc, parsed.path, parsed.query, parsed.fragment)
    )


class BusinessForm(BaseModel):
    """Validates the initial information submitted by a user about their business."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    business_name_and_description: str = Field(
        ...,
        min_length=20,
        max_length=500,
        description="Business name and what the business does (20-500 characters).",
    )
    product_or_service: str = Field(
        ...,
        min_length=3,
        max_length=300,
        description="Core product or service offering provided by the business (3-300 characters).",
    )
    target_audience: str | None = Field(
        default=None,
        max_length=300,
        description="Target audience or ideal customer profile. Required unless audience_unknown is True.",
    )
    audience_unknown: bool = Field(
        default=False,
        description="Flag indicating user is unsure about their audience. If True, target_audience is optional.",
    )
    business_website: str = Field(
        ...,
        description="Primary business website URL (http/https). Must contain a valid domain with a dot.",
    )

    @field_validator("business_website", mode="after")
    @classmethod
    def validate_business_website(cls, v: str) -> str:
        """Validate and normalize the business website URL."""
        return _normalize_and_validate_web_url(v, field_label="Business website")

    @model_validator(mode="after")
    def validate_audience(self) -> Self:
        """Ensure target_audience is provided unless audience_unknown is True."""
        if self.target_audience is not None and not self.target_audience.strip():
            self.target_audience = None

        if not self.audience_unknown and not self.target_audience:
            raise ValueError(
                "Target audience is required unless 'I'm not sure' (audience_unknown) is selected."
            )
        return self
