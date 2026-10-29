from contextlib import asynccontextmanager
import logging
import sys
from fastapi import FastAPI

from backend.app.api.routes.business_overview import (
    router as business_overview_router,
)
from backend.app.api.routes.health import router as health_router
from backend.app.core.config import settings

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Configure centralized application logging.

    Ensures that root and application loggers (backend.*) stream formatted
    logs to sys.stdout at the configured LOG_LEVEL, making them visible in
    the console when running under Uvicorn or test runners.
    """
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    log_format = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Configure the root logger to output to stdout
    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt=date_format,
        stream=sys.stdout,
        force=True,
    )

    # Ensure backend logger hierarchy is set to the configured level
    backend_logger = logging.getLogger("backend")
    backend_logger.setLevel(log_level)


# Initialize logging immediately on module import
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Re-apply logging setup upon server lifespan startup in case server runner reset handlers
    setup_logging()
    logger.info("Competitor Marketing Agent backend started (LOG_LEVEL=%s)", settings.LOG_LEVEL)
    yield
    logger.info("Competitor Marketing Agent backend shutting down")


app = FastAPI(lifespan=lifespan)

app.include_router(health_router)
app.include_router(business_overview_router)

