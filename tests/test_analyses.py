from datetime import datetime

from backend.models.db import Analysis, SourceSnapshot
from backend.repositories import analyses, sources
from test_sources import create, source_api  # Reuse the isolated API/SQLite fixture.


def test_history_get_and_competitor_detail(source_api, analysis_payload):
    api = source_api
    competitor_url = f"/api/v2/competitors/{api.competitor_id}"
    empty_detail = api.client.get(competitor_url).json()
    assert empty_detail["sources"] == []
    assert empty_detail["latest_analysis"] is None
    assert api.client.get(competitor_url + "/analyses").json() == []
    initial = create(api).json()
    source_id = initial["source"]["id"]
    snapshot_id = initial["snapshots"][0]["id"]
    for _ in range(2):
        assert api.client.post(f"/api/v2/sources/{source_id}/reanalyze").status_code == 201
    response = api.client.get(competitor_url + "/analyses")
    assert response.status_code == 200
    history = response.json()
    assert len(history) == 3
    assert history == sorted(history, key=lambda row: (row["created_at"], row["id"]), reverse=True)
    assert {row["snapshot_id"] for row in history} == {snapshot_id}
    for row in history:
        individual = api.client.get(f"/api/v2/analyses/{row['id']}")
        assert individual.status_code == 200
        assert individual.json() == row
        assert row["result_json"] == analysis_payload
    detail = api.client.get(competitor_url).json()
    assert detail["sources"] == [initial["source"]]
    assert detail["latest_analysis"] == history[0]
    assert api.client.get(f"/api/v2/sources/{source_id}").json()["analyses"] == history
    # POST/PATCH/list retain their existing flat response contract.
    patched = api.client.patch(competitor_url, json={"notes": "Note"}).json()
    assert "sources" not in patched and "latest_analysis" not in patched
    listing = api.client.get("/api/v2/competitors").json()
    assert listing == [patched]
    with api.sessions() as db:
        assert db.query(SourceSnapshot).count() == 1


def test_history_tie_breaker_is_deterministic(source_api):
    api = source_api
    initial = create(api).json()
    source_id = initial["source"]["id"]
    api.client.post(f"/api/v2/sources/{source_id}/reanalyze")
    with api.sessions() as db:
        for analysis in db.query(Analysis).all():
            analysis.created_at = datetime(2026, 1, 1)
        db.commit()
        ids = [row.id for row in analyses.list_competitor_analyses(db, api.competitor_id)]
        assert ids == sorted(ids, reverse=True)
        assert analyses.get_latest_competitor_analysis(db, api.competitor_id).id == ids[0]
    history = api.client.get(f"/api/v2/competitors/{api.competitor_id}/analyses").json()
    assert [row["id"] for row in history] == ids
    detail = api.client.get(f"/api/v2/competitors/{api.competitor_id}").json()
    assert detail["latest_analysis"]["id"] == ids[0]


def test_history_unknown_resources_and_openapi(source_api):
    api = source_api
    for path in ("/api/v2/competitors/missing/analyses", "/api/v2/analyses/missing", "/api/v2/competitors/missing"):
        response = api.client.get(path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
    schema = api.client.get("/openapi.json").json()
    paths = schema["paths"]
    item = paths["/api/v2/analyses/{analysis_id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert item["$ref"].endswith("/AnalysisResponse")
    listing = paths["/api/v2/competitors/{competitor_id}/analyses"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert listing["items"]["$ref"].endswith("/AnalysisResponse")
    detail = paths["/api/v2/competitors/{competitor_id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert detail["$ref"].endswith("/CompetitorDetailResponse")
    assert "post" in paths["/api/v2/sources/{source_id}/reanalyze"]
    assert not any("aggregate" in path or "comparison" in path for path in paths)


def test_reanalyze_uses_latest_snapshot(source_api):
    api = source_api
    initial = create(api).json()
    source_id = initial["source"]["id"]
    with api.sessions() as db:
        newer = sources.create_snapshot(
            db, source_id=source_id, extracted_text="Новый актуальный текст источника",
            captured_at=datetime(2099, 1, 1), metadata={"source_id": source_id},
        )
        snapshot_id = newer.id
        db.commit()
    response = api.client.post(f"/api/v2/sources/{source_id}/reanalyze")
    assert response.status_code == 201
    assert len(response.json()["snapshots"]) == 2
    assert response.json()["analyses"][0]["snapshot_id"] == snapshot_id
    assert api.boundary.await_args.args[0].text_context == "Новый актуальный текст источника"
