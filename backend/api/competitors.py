"""Competitor CRUD with API-owned transaction boundaries."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.api import CompetitorCreate, CompetitorResponse, CompetitorUpdate
from backend.repositories import competitors as repository

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


@router.get("/{competitor_id}", response_model=CompetitorResponse)
def get_competitor(competitor_id: str, db: Session = Depends(get_db)):
    competitor = repository.get_competitor(db, competitor_id)
    if competitor is None:
        raise HTTPException(status_code=404, detail="Competitor not found")
    return competitor


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
    try:
        if not repository.delete_competitor(db, competitor_id):
            raise HTTPException(status_code=404, detail="Competitor not found")
        db.commit()
    except Exception:
        db.rollback()
        raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)
