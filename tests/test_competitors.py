from datetime import datetime
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.database import Base, get_db
from backend.repositories import competitors as repository

URL = "/api/v2/competitors"


@pytest.fixture
def competitor_client(app, tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'competitors.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        with sessions() as db:
            yield db

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        engine.dispose()


def test_create_list_get_and_delete(competitor_client):
    client = competitor_client
    assert client.get(URL).json() == []
    payload = {
        "name": "Alpha", "website_url": "https://alpha.example/",
        "niche": "AI", "notes": "First competitor",
    }
    response = client.post(URL, json=payload)
    assert response.status_code == 201
    first = response.json()
    UUID(first["id"])
    assert {key: first[key] for key in payload} == payload
    for field in ("created_at", "updated_at"):
        datetime.fromisoformat(first[field])
    second_response = client.post(URL, json={"name": "Beta"})
    assert second_response.status_code == 201
    second = second_response.json()
    listing = client.get(URL)
    assert listing.status_code == 200
    assert {item["id"] for item in listing.json()} == {first["id"], second["id"]}
    item_url = f"{URL}/{first['id']}"
    loaded = client.get(item_url)
    assert loaded.status_code == 200
    assert loaded.json() == first
    deleted = client.delete(item_url)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(item_url).status_code == 404
    assert client.delete(item_url).status_code == 404
    assert [item["id"] for item in client.get(URL).json()] == [second["id"]]


def test_patch_preserves_omitted_fields_and_clears_nulls(competitor_client):
    client = competitor_client
    created = client.post(URL, json={
        "name": "Alpha", "notes": "Keep", "niche": "AI",
        "website_url": "https://alpha.example/",
    }).json()
    item_url = f"{URL}/{created['id']}"
    empty = client.patch(item_url, json={})
    assert empty.status_code == 200
    assert empty.json() == created
    response = client.patch(item_url, json={"name": "Updated", "niche": "Automation"})
    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "Updated"
    assert updated["niche"] == "Automation"
    assert updated["notes"] == "Keep"
    assert updated["website_url"] == created["website_url"]
    assert updated["created_at"] == created["created_at"]
    assert datetime.fromisoformat(updated["updated_at"]) >= datetime.fromisoformat(created["updated_at"])
    assert client.get(item_url).json() == updated
    cleared = client.patch(item_url, json={"notes": None})
    assert cleared.status_code == 200
    assert client.get(item_url).json()["notes"] is None
    for field in ("website_url", "niche"):
        assert client.patch(item_url, json={field: None}).status_code == 200
        assert client.get(item_url).json()[field] is None


def test_missing_competitor(competitor_client):
    item_url = f"{URL}/{UUID(int=0)}"
    assert competitor_client.get(item_url).status_code == 404
    assert competitor_client.patch(item_url, json={"notes": None}).status_code == 404
    assert competitor_client.delete(item_url).status_code == 404


@pytest.mark.parametrize("payload", [
    {"name": ""}, {"name": "A" * 121}, {"name": None},
    {"name": "Alpha", "unknown": True},
    {"name": "Alpha", "website_url": "not-a-url"},
    {"name": "Alpha", "niche": "A" * 161},
    {"name": "Alpha", "notes": "A" * 2001},
])
def test_create_validation(competitor_client, payload):
    assert competitor_client.post(URL, json=payload).status_code == 422
    assert competitor_client.get(URL).json() == []


@pytest.mark.parametrize("payload", [
    {"name": ""}, {"name": "A" * 121}, {"name": None},
    {"unknown": True}, {"website_url": "not-a-url"},
    {"niche": "A" * 161}, {"notes": "A" * 2001},
])
def test_patch_validation(competitor_client, payload):
    created = competitor_client.post(URL, json={"name": "Alpha"}).json()
    item_url = f"{URL}/{created['id']}"
    assert competitor_client.patch(item_url, json=payload).status_code == 422
    assert competitor_client.get(item_url).json() == created


@pytest.mark.parametrize("method,operation", [
    ("post", "create_competitor"),
    ("patch", "update_competitor"),
    ("delete", "delete_competitor"),
])
@pytest.mark.parametrize("failure", ["repository", "commit"])
def test_write_failure_rolls_back(competitor_client, monkeypatch, method, operation, failure):
    client = competitor_client
    created = client.post(URL, json={"name": "Original"}).json()
    item_url = f"{URL}/{created['id']}"
    rollback = Session.rollback
    rollback_calls = Mock()

    def tracked_rollback(db):
        rollback_calls()
        return rollback(db)

    def fail_commit(db):
        raise RuntimeError("Injected commit failure")

    original_operation = getattr(repository, operation)

    def fail_repository(*args, **kwargs):
        original_operation(*args, **kwargs)
        raise RuntimeError("Injected repository failure")

    with monkeypatch.context() as patch:
        patch.setattr(Session, "rollback", tracked_rollback)
        if failure == "commit":
            patch.setattr(Session, "commit", fail_commit)
        else:
            patch.setattr(repository, operation, fail_repository)
        with pytest.raises(RuntimeError, match="Injected"):
            if method == "delete":
                client.delete(item_url)
            else:
                client.request(method, URL if method == "post" else item_url, json={"name": "Changed"})
        rollback_calls.assert_called_once_with()
    assert client.get(item_url).json() == created
    assert len(client.get(URL).json()) == 1


def test_openapi_models_and_routes(competitor_client):
    response = competitor_client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    paths = schema["paths"]
    assert {"get", "post"} <= paths[URL].keys()
    assert {"get", "patch", "delete"} <= paths[f"{URL}/{{competitor_id}}"].keys()
    assert {"/api/v2/health", "/health", "/analyze_text", "/analyze_image", "/parse_demo", "/history"} <= paths.keys()
    for path, method, model in [
        (URL, "post", "CompetitorCreate"),
        (f"{URL}/{{competitor_id}}", "patch", "CompetitorUpdate"),
    ]:
        assert paths[path][method]["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(f"/{model}")
    assert paths[URL]["post"]["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/CompetitorResponse")
    assert paths[URL]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["items"]["$ref"].endswith("/CompetitorResponse")
