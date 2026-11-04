"""Database metadata aggregation for Alembic and model discovery."""

from backend.app.db.session import Base
from backend.app.models.business_overview import BusinessOverviewModel

__all__ = [
    "Base",
    "BusinessOverviewModel",
]

