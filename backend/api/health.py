"""Application health endpoint."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.config import settings

router = APIRouter(
    prefix="/api/v2",
    tags=["health"],
)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: Literal["2.0.0"] = "2.0.0"
    database: Literal[
        "ready",
        "not_initialized",
    ]
    browser: Literal["not_initialized"] = "not_initialized"
    ai_configured: bool


@router.get(
    "/health",
    response_model=HealthResponse,
)
async def health(
    request: Request,
) -> HealthResponse:
    database_ready = getattr(
        request.app.state,
        "database_ready",
        False,
    )

    return HealthResponse(
        database=(
            "ready"
            if database_ready
            else "not_initialized"
        ),
        ai_configured=settings.ai_configured,
    )
