"""Validated analysis history and competitor-level aggregation."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.errors import V2Route, ERROR_RESPONSES
from backend.database import get_db
from backend.models.api import AnalysisResponse
from backend.repositories import analyses, competitors
from backend.services.ingestion_service import ResourceNotFound
from backend.services.analysis_service import analysis_service

router = APIRouter(
    prefix="/api/v2", tags=["analyses"], route_class=V2Route,
    responses=ERROR_RESPONSES,
)


@router.get("/competitors/{competitor_id}/analyses", response_model=list[AnalysisResponse])
def list_analyses(competitor_id: str, db: Session = Depends(get_db)):
    if competitors.get_competitor(db, competitor_id) is None:
        raise ResourceNotFound("Competitor not found")
    return analyses.list_competitor_analyses(db, competitor_id)


@router.get("/analyses/{analysis_id}", response_model=AnalysisResponse)
def get_analysis(analysis_id: str, db: Session = Depends(get_db)):
    analysis = analyses.get_analysis(db, analysis_id)
    if analysis is None:
        raise ResourceNotFound("Analysis not found")
    return analysis


@router.post("/competitors/{competitor_id}/aggregate-analysis", response_model=AnalysisResponse, status_code=201)
async def aggregate_analysis(competitor_id: str, db: Session = Depends(get_db)):
    return await analysis_service.aggregate_competitor(db, competitor_id)
