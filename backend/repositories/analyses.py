"""Persist validated v2 analyses without owning commit/rollback."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.analysis import CompetitorAnalysis
from backend.models.db import Analysis, SourceSnapshot


def create_analysis(db: Session, *, result: CompetitorAnalysis, **fields) -> Analysis:
    validated = CompetitorAnalysis.model_validate_json(result.model_dump_json(), strict=True)
    analysis = Analysis(result_json=validated.model_dump_json(), **fields)
    db.add(analysis)
    db.flush()
    db.refresh(analysis)
    return analysis


def get_analysis(db: Session, analysis_id: str) -> Analysis | None:
    return db.get(Analysis, analysis_id)


def list_source_analyses(db: Session, source_id: str) -> list[Analysis]:
    return list(db.scalars(
        select(Analysis).join(SourceSnapshot).where(SourceSnapshot.source_id == source_id)
        .order_by(Analysis.created_at.desc(), Analysis.id.desc())
    ))


def list_competitor_analyses(db: Session, competitor_id: str) -> list[Analysis]:
    return list(db.scalars(
        select(Analysis).where(Analysis.competitor_id == competitor_id)
        .order_by(Analysis.created_at.desc(), Analysis.id.desc())
    ))


def get_latest_competitor_analysis(db: Session, competitor_id: str) -> Analysis | None:
    return db.scalar(
        select(Analysis).where(Analysis.competitor_id == competitor_id)
        .order_by(Analysis.created_at.desc(), Analysis.id.desc()).limit(1)
    )


def get_latest_snapshot_analysis(db: Session, snapshot_id: str) -> Analysis | None:
    return db.scalar(
        select(Analysis).where(Analysis.snapshot_id == snapshot_id, Analysis.analysis_type == "source")
        .order_by(Analysis.created_at.desc(), Analysis.id.desc()).limit(1)
    )


def get_latest_aggregate(db: Session, competitor_id: str) -> Analysis | None:
    return db.scalar(
        select(Analysis).where(Analysis.competitor_id == competitor_id, Analysis.analysis_type == "aggregate")
        .order_by(Analysis.created_at.desc(), Analysis.id.desc()).limit(1)
    )
