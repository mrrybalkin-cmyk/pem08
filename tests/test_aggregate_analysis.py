import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.models.analysis import CompetitorAnalysis
from backend.models.db import Analysis, Source, SourceSnapshot
from backend.repositories import analyses, sources
from backend.services.ai_service import AIConfigurationError, AIProviderError, AIProviderTimeoutError, AIResponseError, ai_service
from backend.services.analysis_service import analysis_service
from backend.services.prompt_service import AGGREGATE_PROMPT_VERSION
from test_sources import source_api


@pytest.fixture
def aggregate_api(source_api, monkeypatch, analysis_payload):
    api = source_api
    api.aggregate = AsyncMock(return_value=CompetitorAnalysis.model_validate(analysis_payload))
    monkeypatch.setattr(ai_service, "aggregate_competitor", api.aggregate)
    # Stage 7 must never recapture/read source artifacts.
    monkeypatch.setattr("backend.services.browser_service.browser_service.capture", AsyncMock(side_effect=AssertionError("Browser called")))
    monkeypatch.setattr("backend.services.storage_service.StorageService.read", Mock(side_effect=AssertionError("Upload read")))
    return api


def seed_source(api, kind="text", *, competitor_id=None, analyzed=True):
    with api.sessions() as db:
        source = sources.create_source(db, competitor_id=competitor_id or api.competitor_id, source_type=kind, label=kind)
        snapshot = sources.create_snapshot(db, source_id=source.id, metadata={}, captured_at=datetime(2026, 1, 1))
        row = analyses.create_analysis(db, competitor_id=source.competitor_id, snapshot_id=snapshot.id,
            analysis_type="source", model_id="source-model", prompt_version="source-version",
            result=api.aggregate.return_value, created_at=datetime(2026, 1, 1)) if analyzed else None
        ids = source.id, snapshot.id, row.id if row else None
        db.commit()
        return ids


def aggregate(api, cid=None):
    return api.client.post(f"/api/v2/competitors/{cid or api.competitor_id}/aggregate-analysis")


def aggregate_count(api):
    with api.sessions() as db:
        return db.query(Analysis).filter_by(analysis_type="aggregate").count()


def test_current_sources_latest_reanalysis_and_unanalysed_refresh(aggregate_api):
    api = aggregate_api
    ids = [seed_source(api, kind) for kind in ("text", "image", "pdf", "url")]
    url_sid, old_snapshot, old_analysis = ids[-1]
    text_sid, text_snapshot, old_text_analysis = ids[0]
    with api.sessions() as db:
        current = sources.create_snapshot(db, source_id=url_sid, captured_at=datetime(2026, 1, 2), metadata={})
        current_id = current.id
        newest_text = analyses.create_analysis(db, competitor_id=api.competitor_id, snapshot_id=text_snapshot,
            analysis_type="source", model_id="test", prompt_version="test", result=api.aggregate.return_value,
            created_at=datetime(2026, 1, 2))
        newest_text_id = newest_text.id
        db.commit()
    response = aggregate(api)
    assert response.status_code == 201, response.text
    prepared = api.aggregate.await_args.args[0]
    assert {item.source_id for item in prepared.sources} == {row[0] for row in ids[:-1]}
    assert old_analysis not in {item.analysis_id for item in prepared.sources}
    assert next(item for item in prepared.sources if item.source_id == text_sid).analysis_id == newest_text_id
    assert prepared.coverage.total_sources == 4
    assert prepared.coverage.sources_with_current_analysis == 3
    assert prepared.coverage.sources_without_current_analysis == 1
    assert prepared.coverage.omitted_source_ids == (url_sid,)
    with api.sessions() as db:
        new_url = analyses.create_analysis(db, competitor_id=api.competitor_id, snapshot_id=current_id,
            analysis_type="source", model_id="test", prompt_version="test", result=api.aggregate.return_value)
        new_url_id = new_url.id
        db.commit()
    assert aggregate(api).status_code == 201
    prepared = api.aggregate.await_args.args[0]
    url = next(item for item in prepared.sources if item.source_id == url_sid)
    assert url.snapshot_id == current_id and url.analysis_id == new_url_id
    assert url.snapshot_id != old_snapshot and url.analysis_id != old_analysis
    assert prepared.coverage.sources_without_current_analysis == 0
    assert aggregate_count(api) == 2
    assert not list(api.uploads.glob("*"))


def test_aggregate_history_persistence_detail_and_source_deletion(aggregate_api):
    api = aggregate_api
    sid, snapshot_id, source_analysis_id = seed_source(api)
    responses = [aggregate(api).json(), aggregate(api).json()]
    for row in responses:
        assert row["analysis_type"] == "aggregate" and row["snapshot_id"] is None
        assert row["prompt_version"] == AGGREGATE_PROMPT_VERSION
        with api.sessions() as db:
            persisted = db.get(Analysis, row["id"])
            assert CompetitorAnalysis.model_validate_json(persisted.result_json).model_dump(mode="json") == row["result_json"]
            assert db.query(Source).count() == db.query(SourceSnapshot).count() == 1
            assert db.get(Analysis, source_analysis_id).snapshot_id == snapshot_id
    history = api.client.get(f"/api/v2/competitors/{api.competitor_id}/analyses").json()
    assert len(history) == 3
    assert history == sorted(history, key=lambda item: (item["created_at"], item["id"]), reverse=True)
    assert api.client.get(f"/api/v2/competitors/{api.competitor_id}").json()["latest_analysis"] == history[0]
    assert api.client.delete(f"/api/v2/sources/{sid}").status_code == 204
    assert aggregate_count(api) == 2
    api.aggregate.reset_mock()
    assert aggregate(api).status_code == 400
    api.aggregate.assert_not_awaited()


@pytest.mark.parametrize("state,status", [("missing", 404), ("empty", 400), ("unanalysed", 400), ("stale", 400)])
def test_aggregate_not_ready_never_calls_ai(aggregate_api, state, status):
    api = aggregate_api
    if state == "unanalysed":
        seed_source(api, analyzed=False)
    elif state == "stale":
        sid, _, _ = seed_source(api, "url")
        with api.sessions() as db:
            sources.create_snapshot(db, source_id=sid, metadata={}, captured_at=datetime(2026, 1, 2))
            db.commit()
    response = aggregate(api, "missing" if state == "missing" else None)
    assert response.status_code == status
    assert "error" in response.json()
    api.aggregate.assert_not_awaited()
    assert aggregate_count(api) == 0


@pytest.mark.parametrize("failure,status", [
    (AIConfigurationError("missing"), 500), (AIProviderError("failed"), 502),
    (AIProviderTimeoutError("timeout"), 504), (AIResponseError("invalid"), 502),
])
def test_aggregate_ai_failure_preserves_sources(aggregate_api, failure, status):
    api = aggregate_api
    seed_source(api)
    api.aggregate.side_effect = failure
    assert aggregate(api).status_code == status
    assert aggregate_count(api) == 0
    with api.sessions() as db:
        assert db.query(Source).count() == db.query(SourceSnapshot).count() == db.query(Analysis).count() == 1


def test_aggregate_db_failure_rolls_back(aggregate_api, monkeypatch):
    api = aggregate_api
    seed_source(api)
    monkeypatch.setattr(Session, "commit", Mock(side_effect=SQLAlchemyError("injected")))
    assert aggregate(api).status_code == 500
    assert aggregate_count(api) == 0


@pytest.mark.asyncio
async def test_aggregate_no_transaction_during_ai_and_cancellation(aggregate_api):
    api = aggregate_api
    seed_source(api)
    with api.sessions() as db:
        async def cancel(prepared):
            assert not db.in_transaction()
            raise asyncio.CancelledError()
        api.aggregate.side_effect = cancel
        with pytest.raises(asyncio.CancelledError):
            await analysis_service.aggregate_competitor(db, api.competitor_id)
    assert aggregate_count(api) == 0


def test_aggregate_invalid_mock_response_is_revalidated(aggregate_api):
    api = aggregate_api
    seed_source(api)
    api.aggregate.return_value.scorecard.trust.score = 11
    assert aggregate(api).status_code == 502
    assert aggregate_count(api) == 0


def test_deleted_source_is_omitted_without_rewriting_history(aggregate_api):
    api = aggregate_api
    first = seed_source(api)
    second = seed_source(api, "image")
    old = aggregate(api).json()
    assert api.client.delete(f"/api/v2/sources/{first[0]}").status_code == 204
    assert aggregate(api).status_code == 201
    assert api.aggregate.await_args.args[0].coverage.included_source_ids == (second[0],)
    assert api.client.get(f"/api/v2/analyses/{old['id']}").json() == old


def test_aggregate_selection_ties_are_deterministic(aggregate_api):
    api = aggregate_api
    sid, _, _ = seed_source(api)
    with api.sessions() as db:
        for snapshot_id in ("tie-a", "tie-z"):
            sources.create_snapshot(db, id=snapshot_id, source_id=sid, metadata={}, captured_at=datetime(2030, 1, 1))
        for analysis_id in ("analysis-a", "analysis-z"):
            analyses.create_analysis(db, id=analysis_id, competitor_id=api.competitor_id,
                snapshot_id="tie-z", analysis_type="source", model_id="test", prompt_version="test",
                result=api.aggregate.return_value, created_at=datetime(2030, 1, 1))
        db.commit()
    assert aggregate(api).status_code == 201
    selected = api.aggregate.await_args.args[0].sources[0]
    assert selected.snapshot_id == "tie-z" and selected.analysis_id == "analysis-z"
