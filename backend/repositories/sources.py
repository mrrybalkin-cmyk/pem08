"""Source/snapshot operations within the caller's transaction."""

import json
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.db import Source, SourceSnapshot


def create_source(db: Session, **fields) -> Source:
    source = Source(**fields)
    db.add(source)
    db.flush()
    db.refresh(source)
    return source


def create_snapshot(db: Session, *, metadata: dict, **fields) -> SourceSnapshot:
    snapshot = SourceSnapshot(
        metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        **fields,
    )
    db.add(snapshot)
    db.flush()
    db.refresh(snapshot)
    return snapshot


def get_source(db: Session, source_id: str) -> Source | None:
    return db.get(Source, source_id)


def list_sources(db: Session, competitor_id: str) -> list[Source]:
    return list(db.scalars(select(Source).where(Source.competitor_id == competitor_id).order_by(Source.created_at, Source.id)))


def list_snapshots(db: Session, source_id: str) -> list[SourceSnapshot]:
    return list(db.scalars(select(SourceSnapshot).where(SourceSnapshot.source_id == source_id).order_by(SourceSnapshot.captured_at, SourceSnapshot.id)))


def get_snapshot(db: Session, snapshot_id: str) -> SourceSnapshot | None:
    return db.get(SourceSnapshot, snapshot_id)


def get_latest_snapshot(db: Session, source_id: str) -> SourceSnapshot | None:
    return db.scalar(
        select(SourceSnapshot).where(SourceSnapshot.source_id == source_id)
        .order_by(SourceSnapshot.captured_at.desc(), SourceSnapshot.id.desc()).limit(1)
    )


def delete_source(db: Session, source_id: str) -> bool:
    source = get_source(db, source_id)
    if source is None:
        return False
    db.delete(source)
    db.flush()
    return True
