from fastapi import FastAPI

from backend.app.api.routes.business_overview import (
    router as business_overview_router,
)
from backend.app.api.routes.health import router as health_router

app = FastAPI()

app.include_router(health_router)
app.include_router(business_overview_router)
