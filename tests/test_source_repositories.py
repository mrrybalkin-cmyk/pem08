import json

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database import Base
from backend.models.analysis import CompetitorAnalysis
from backend.models.db import Analysis, Comparison, Source, SourceSnapshot
from backend.repositories import analyses, competitors, sources


@pytest.fixture
def source_engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'sources.db').as_posix()}")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def test_stage4_table_contract(source_engine):
    assert set(inspect(source_engine).get_table_names()) == {"competitors", "sources", "source_snapshots", "analyses", "comparisons"}
    assert set(Source.__table__.columns.keys()) == {
        "id", "competitor_id", "source_type", "label", "original_filename", "mime_type", "url", "storage_path", "sha256", "created_at",
    }
    assert set(SourceSnapshot.__table__.columns.keys()) == {
        "id", "source_id", "captured_at", "final_url", "title", "meta_description", "extracted_text", "screenshot_path", "secondary_screenshot_path", "metadata_json",
    }
    assert set(Analysis.__table__.columns.keys()) == {
        "id", "competitor_id", "snapshot_id", "analysis_type", "model_id", "prompt_version", "result_json", "input_tokens", "output_tokens", "duration_ms", "created_at",
    }
    assert set(Comparison.__table__.columns.keys()) == {
        "id", "competitor_ids_json", "model_id", "result_json", "created_at",
    }
    with source_engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_repositories_persistence_relations_and_rollback(source_engine, analysis_payload):
    result = CompetitorAnalysis.model_validate(analysis_payload)
    with Session(source_engine) as db:
        competitor = competitors.create_competitor(db, name="Alpha")
        competitor_id = competitor.id
        db.commit()
        source = sources.create_source(db, competitor_id=competitor_id, source_type="text", label="Text")
        source_id = source.id
        snapshot = sources.create_snapshot(db, source_id=source.id, extracted_text="Автоматизация для команд", metadata={"z": "Я", "a": 1})
        analysis = analyses.create_analysis(
            db, competitor_id=competitor_id, snapshot_id=snapshot.id, analysis_type="source",
            model_id="test", prompt_version="test", result=result,
        )
        assert source.competitor.id == competitor_id
        assert snapshot.source.id == source_id
        assert analysis.snapshot.id == snapshot.id
        assert json.loads(snapshot.metadata_json) == {"a": 1, "z": "Я"}
        assert snapshot.metadata_json == '{"a":1,"z":"Я"}'
        assert CompetitorAnalysis.model_validate_json(analysis.result_json) == result
        assert analyses.get_analysis(db, analysis.id).id == analysis.id
        db.rollback()
    with Session(source_engine) as db:
        assert sources.get_source(db, source_id) is None
        assert db.query(SourceSnapshot).count() == 0
        assert db.query(Analysis).count() == 0
        source = sources.create_source(db, competitor_id=competitor_id, source_type="text", label="Persist")
        source_id = source.id
        snapshot = sources.create_snapshot(db, source_id=source_id, metadata={})
        analyses.create_analysis(db, competitor_id=competitor_id, snapshot_id=snapshot.id, analysis_type="source", model_id="test", prompt_version="test", result=result)
        db.commit()
    with Session(source_engine) as db:
        assert len(sources.list_sources(db, competitor_id)) == 1
        assert len(sources.list_snapshots(db, source_id)) == 1
        assert len(analyses.list_source_analyses(db, source_id)) == 1
        assert sources.delete_source(db, source_id)
        db.rollback()
    with Session(source_engine) as db:
        assert sources.get_source(db, source_id) is not None
        assert sources.delete_source(db, source_id)
        db.commit()
        assert db.query(SourceSnapshot).count() == 0
        assert db.query(Analysis).count() == 0
        assert not sources.delete_source(db, "missing")


def test_fk_enforcement_and_competitor_cascade(source_engine, analysis_payload):
    with Session(source_engine) as db:
        with pytest.raises(IntegrityError):
            sources.create_source(db, competitor_id="missing", source_type="text", label="Invalid")
        db.rollback()
        competitor = competitors.create_competitor(db, name="Alpha")
        source = sources.create_source(db, competitor_id=competitor.id, source_type="image", label="Image")
        snapshot = sources.create_snapshot(db, source_id=source.id, metadata={})
        analyses.create_analysis(db, competitor_id=competitor.id, snapshot_id=snapshot.id, analysis_type="source", model_id="test", prompt_version="test", result=CompetitorAnalysis.model_validate(analysis_payload))
        db.commit()
        assert competitors.delete_competitor(db, competitor.id)
        db.commit()
        for model in (Source, SourceSnapshot, Analysis):
            assert db.query(model).count() == 0
