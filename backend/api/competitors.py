"""Competitor CRUD with API-owned transaction boundaries."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.api import CompetitorCreate, CompetitorDetailResponse, CompetitorResponse, CompetitorUpdate, SourceErrorResponse
from backend.repositories import competitors as repository
from backend.services.ingestion_service import delete_competitor as delete_competitor_with_files
from backend.services.ingestion_service import ResourceNotFound, competitor_detail

router = APIRouter(prefix="/api/v2/competitors", tags=["competitors"])


@router.post("", response_model=CompetitorResponse, status_code=status.HTTP_201_CREATED)
def create_competitor(payload: CompetitorCreate, db: Session = Depends(get_db)):
    try:
        competitor = repository.create_competitor(db, **payload.model_dump(mode="json"))
        response = CompetitorResponse.model_validate(competitor)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return response


@router.get("", response_model=list[CompetitorResponse])
def list_competitors(db: Session = Depends(get_db)):
    return repository.list_competitors(db)


@router.get("/{competitor_id}", response_model=CompetitorDetailResponse, responses={404: {"model": SourceErrorResponse}, 500: {"model": SourceErrorResponse}})
def get_competitor(competitor_id: str, db: Session = Depends(get_db)):
    try:
        return competitor_detail(db, competitor_id)
    except ResourceNotFound:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "Competitor not found", "details": None}})
    except Exception:
        return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR", "message": "Competitor detail failed", "details": None}})


@router.patch("/{competitor_id}", response_model=CompetitorResponse)
def update_competitor(
    competitor_id: str, payload: CompetitorUpdate, db: Session = Depends(get_db),
):
    try:
        competitor = repository.update_competitor(
            db, competitor_id, payload.model_dump(mode="json", exclude_unset=True),
        )
        if competitor is None:
            raise HTTPException(status_code=404, detail="Competitor not found")
        response = CompetitorResponse.model_validate(competitor)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return response


@router.delete("/{competitor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_competitor(competitor_id: str, db: Session = Depends(get_db)):
    if not delete_competitor_with_files(db, competitor_id):
        raise HTTPException(status_code=404, detail="Competitor not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
