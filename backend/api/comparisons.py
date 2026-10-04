"""Stage 7 HTTP boundary; no queries or provider details."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.api.errors import V2Route, ERROR_RESPONSES
from backend.database import get_db
from backend.models.analysis import ComparisonResult
from backend.models.api import ComparisonRequest
from backend.services.analysis_service import analysis_service

router = APIRouter(
    prefix="/api/v2", tags=["comparisons"], route_class=V2Route,
    responses=ERROR_RESPONSES,
)


@router.post("/comparisons", response_model=ComparisonResult, status_code=201)
async def compare(payload: ComparisonRequest, db: Session = Depends(get_db)):
    return await analysis_service.compare_competitors(db, payload.competitor_ids)
