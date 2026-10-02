"""Liveness only: no provider, database or browser probes."""
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from backend.config import settings

router = APIRouter(prefix="/api/v2", tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: Literal["2.0.0"] = "2.0.0"
    database: Literal["not_initialized"] = "not_initialized"
    browser: Literal["not_initialized"] = "not_initialized"
    ai_configured: bool


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(ai_configured=settings.ai_configured)
