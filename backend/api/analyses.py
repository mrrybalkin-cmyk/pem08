"""Read-only validated analysis history; no aggregate or comparison pipeline."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.sources import SourceRoute
from backend.database import get_db
from backend.models.api import AnalysisResponse, SourceErrorResponse
from backend.repositories import analyses, competitors
from backend.services.ingestion_service import ResourceNotFound

router = APIRouter(
    prefix="/api/v2", tags=["analyses"], route_class=SourceRoute,
    responses={status: {"model": SourceErrorResponse} for status in (404, 500)},
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
