"""SQLAlchemy model for Business Overview persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import DateTime, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.session import Base


class BusinessOverviewModel(Base):
    """Database model storing synthesized business overviews.

    Every successful generation creates a new immutable record.
    """

    __tablename__ = "business_overviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        doc="Unique identifier (UUID) for this business overview record.",
    )
    business_name: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        doc="Name of the business extracted or synthesized.",
    )
    website_url: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="Primary business website URL analyzed.",
    )
    overview_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        doc="Complete structured BusinessOverview object serialized to JSONB.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        doc="Timezone-aware timestamp when this business overview was created.",
    )

