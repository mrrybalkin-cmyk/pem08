import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models.analysis import ComparisonResult
from backend.models.db import Analysis, Comparison
from backend.repositories import comparisons
from backend.services.ai_service import AIProviderError, AIProviderTimeoutError, AIResponseError, ai_service
from backend.services.analysis_service import analysis_service
from test_aggregate_analysis import aggregate_api, aggregate, seed_source
from test_sources import source_api


def comparison_payload(ids, names=None):
    return {
        "executive_summary": "Сравнение предоставленных исторических профилей",
        "competitors": [{"competitor_id": cid, "competitor_name": (names or {}).get(cid, "Alpha"),
            "positioning": "Автоматизация", "strengths": ["Ясное предложение"], "gaps": ["Мало данных"],
            **{field: 5 for field in ("positioning_clarity", "value_proposition", "trust", "cta_strength")}}
            for cid in ids],
        "shared_patterns": [], "meaningful_differences": [], "market_gaps": [],
        "opportunities": [], "limitations": ["Evidence coverage участников различается"],
    }


@pytest.fixture
def comparison_api(aggregate_api, monkeypatch):
    api = aggregate_api
    api.compare = AsyncMock()
    monkeypatch.setattr(ai_service, "compare_competitors", api.compare)
    return api


def ready_competitors(api, count=2):
    ids = [api.competitor_id]
    for index in range(1, count):
        ids.append(api.client.post("/api/v2/competitors", json={"name": "Alpha"}).json()["id"])
    for cid in ids:
        seed_source(api, competitor_id=cid)
        assert aggregate(api, cid).status_code == 201
    api.compare.return_value = ComparisonResult.model_validate(comparison_payload(ids))
    return ids


def compare(api, ids):
    return api.client.post("/api/v2/comparisons", json={"competitor_ids": ids})


@pytest.mark.parametrize("count", [2, 5])
def test_comparison_persists_order_and_historical_retention(comparison_api, count):
    api = comparison_api
    ids = ready_competitors(api, count)[::-1]
    response = compare(api, ids)
    assert response.status_code == 201, response.text
    result = ComparisonResult.model_validate(response.json())
    assert [row.competitor_id for row in result.competitors] == ids
    prepared = api.compare.await_args.args[0]
    assert [item.competitor_id for item in prepared.competitors] == ids
    assert prepared.competitors[0].result.limitations
    with api.sessions() as db:
        row = db.query(Comparison).one()
        row_id = row.id
        assert json.loads(row.competitor_ids_json) == ids
        assert row.model_id == settings.ai_model
        assert ComparisonResult.model_validate_json(row.result_json, strict=True) == result
        assert comparisons.get_comparison(db, row_id).id == row_id
    assert api.client.delete(f"/api/v2/competitors/{ids[0]}").status_code == 204
    with api.sessions() as db:
        assert json.loads(db.get(Comparison, row_id).competitor_ids_json) == ids


@pytest.mark.parametrize("ids", [[], ["A"], list("ABCDEF"), ["A", "A"], ["A", "A", "B"]])
def test_comparison_cardinality_and_duplicates(comparison_api, ids):
    api = comparison_api
    assert compare(api, ids).status_code == 422
    api.compare.assert_not_awaited()
    with api.sessions() as db:
        assert db.query(Comparison).count() == 0


@pytest.mark.parametrize("mixed", [False, True])
def test_unknown_competitor_precedes_readiness(comparison_api, mixed):
    api = comparison_api
    assert compare(api, [api.competitor_id if mixed else "missing-a", "missing-b"]).status_code == 404
    api.compare.assert_not_awaited()


def test_missing_aggregate_never_substitutes_source_analysis(comparison_api):
    api = comparison_api
    ids = ready_competitors(api)
    with api.sessions() as db:
        for row in db.query(Analysis).filter_by(competitor_id=ids[1], analysis_type="aggregate"):
            db.delete(row)
        db.commit()
    assert compare(api, ids).status_code == 400
    api.compare.assert_not_awaited()


@pytest.mark.parametrize("failure,status", [(AIProviderError("failed"), 502),
    (AIProviderTimeoutError("timeout"), 504), (AIResponseError("invalid"), 502)])
def test_comparison_ai_failure_persists_nothing(comparison_api, failure, status):
    api = comparison_api
    ids = ready_competitors(api)
    api.compare.side_effect = failure
    assert compare(api, ids).status_code == status
    with api.sessions() as db:
        assert db.query(Comparison).count() == 0
        assert db.query(Analysis).filter_by(analysis_type="aggregate").count() == 2


def test_comparison_commit_failure_and_repository_no_commit(comparison_api, monkeypatch):
    api = comparison_api
    ids = ready_competitors(api)
    with api.sessions() as db:
        row = comparisons.create_comparison(db, competitor_ids=ids, model_id="test", result=api.compare.return_value)
        row_id = row.id
        db.rollback()
    with api.sessions() as db:
        assert db.get(Comparison, row_id) is None
    monkeypatch.setattr(Session, "commit", Mock(side_effect=SQLAlchemyError("injected")))
    assert compare(api, ids).status_code == 500
    with api.sessions() as db:
        assert db.query(Comparison).count() == 0


@pytest.mark.asyncio
async def test_comparison_cancellation_and_read_transaction_boundary(comparison_api):
    api = comparison_api
    ids = ready_competitors(api)
    with api.sessions() as db:
        async def cancel(prepared):
            assert not db.in_transaction()
            raise asyncio.CancelledError()
        api.compare.side_effect = cancel
        with pytest.raises(asyncio.CancelledError):
            await analysis_service.compare_competitors(db, ids)
    with api.sessions() as db:
        assert db.query(Comparison).count() == 0


@pytest.mark.parametrize("invalid", ["wrong_id", "duplicate", "wrong_name", "score"])
def test_comparison_invalid_mock_is_revalidated(comparison_api, invalid):
    api = comparison_api
    ids = ready_competitors(api)
    result = api.compare.return_value
    if invalid == "wrong_id":
        result.competitors[0].competitor_id = "invented"
    elif invalid == "duplicate":
        result.competitors[0].competitor_id = ids[1]
    elif invalid == "wrong_name":
        result.competitors[0].competitor_name = "Invented name"
    else:
        result.competitors[0].trust = 11
    assert compare(api, ids).status_code == 502
    with api.sessions() as db:
        assert db.query(Comparison).count() == 0


def test_comparison_latest_aggregate_not_latest_source(comparison_api):
    api = comparison_api
    ids = ready_competitors(api)
    second = aggregate(api).json()
    seed_source(api)  # New source analysis must not replace persisted aggregate.
    assert compare(api, ids).status_code == 201
    profile = api.compare.await_args.args[0].competitors[0]
    assert profile.analysis_id == second["id"]


@pytest.mark.asyncio
async def test_deleted_competitor_during_ai_preserves_historical_comparison(comparison_api):
    from backend.repositories import competitors
    api = comparison_api
    ids = ready_competitors(api)
    output = api.compare.return_value

    async def delete_during_ai(prepared):
        with api.sessions() as other:
            competitors.delete_competitor(other, ids[1])
            other.commit()
        return output

    api.compare.side_effect = delete_during_ai
    with api.sessions() as db:
        result = await analysis_service.compare_competitors(db, ids)
    assert [row.competitor_id for row in result.competitors] == ids
    with api.sessions() as db:
        assert json.loads(db.query(Comparison).one().competitor_ids_json) == ids
