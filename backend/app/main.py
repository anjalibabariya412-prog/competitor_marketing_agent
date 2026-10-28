from fastapi import FastAPI

try:
    from app.api.routes.health import router as health_router
except ModuleNotFoundError:
    from backend.app.api.routes.health import router as health_router

app = FastAPI()

app.include_router(health_router)
