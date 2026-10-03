"""Stage 7 HTTP boundary; no queries or provider details."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.api.sources import SourceRoute
from backend.database import get_db
from backend.models.analysis import ComparisonResult
from backend.models.api import ComparisonRequest, SourceErrorResponse
from backend.services.analysis_service import analysis_service

router = APIRouter(
    prefix="/api/v2", tags=["comparisons"], route_class=SourceRoute,
    responses={status: {"model": SourceErrorResponse} for status in (400, 404, 422, 500, 502, 504)},
)


@router.post("/comparisons", response_model=ComparisonResult, status_code=201)
async def compare(payload: ComparisonRequest, db: Session = Depends(get_db)):
    return await analysis_service.compare_competitors(db, payload.competitor_ids)
