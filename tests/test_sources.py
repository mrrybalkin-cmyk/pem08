import asyncio
import base64
import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.config import settings
from backend.database import Base, get_db
from backend.models.analysis import CompetitorAnalysis
from backend.models.db import Analysis, Source, SourceSnapshot
from backend.repositories import analyses, sources
from backend.models.api import TextSourceCreate
from backend.services.ai_service import AIConfigurationError, AIProviderError, AIProviderTimeoutError, AIResponseError, AIService, ai_service
from backend.services.ingestion_service import ingestion_service
from backend.services.prompt_service import ANALYSIS_PROMPT_VERSION
from backend.services.storage_service import StorageService


def image_bytes(format="PNG"):
    stream = BytesIO()
    Image.new("RGB", (4, 3), "blue").save(stream, format=format)
    return stream.getvalue()


@pytest.fixture
def source_api(app, tmp_path, monkeypatch, analysis_payload):
    engine = create_engine(f"sqlite:///{(tmp_path / 'api.db').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    uploads = tmp_path / "uploads"
    monkeypatch.setattr(settings, "upload_dir", uploads)
    boundary = AsyncMock(return_value=CompetitorAnalysis.model_validate(analysis_payload))
    monkeypatch.setattr(ai_service, "analyze_source", boundary)

    def override_db():
        with sessions() as db:
            yield db

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            competitor_id = client.post("/api/v2/competitors", json={"name": "Alpha"}).json()["id"]
            yield SimpleNamespace(client=client, competitor_id=competitor_id, sessions=sessions, uploads=uploads, boundary=boundary)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        engine.dispose()


def assert_empty(api):
    with api.sessions() as db:
        for model in (Source, SourceSnapshot, Analysis):
            assert db.query(model).count() == 0
    assert list(api.uploads.iterdir()) == []


def assert_retryable(api, count=1):
    with api.sessions() as db:
        persisted = db.query(Source).all()
        assert len(persisted) == count
        assert db.query(SourceSnapshot).count() == count
        assert db.query(Analysis).count() == 0
        ids = [source.id for source in persisted]
    for source_id in ids:
        response = api.client.get(f"/api/v2/sources/{source_id}")
        assert response.status_code == 200
        detail = response.json()
        assert len(detail["snapshots"]) == 1
        assert detail["analyses"] == []
        source = detail["source"]
        if source["source_type"] == "image":
            path = api.uploads / source["storage_path"]
            assert path.read_bytes() == image_bytes()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]


def create(api, kind="text", filename="original.png"):
    root = f"/api/v2/competitors/{api.competitor_id}/sources"
    if kind == "text":
        return api.client.post(root + "/text", json={"text": "Автоматизация для команд"})
    return api.client.post(root + "/file", files={"file": (filename, image_bytes(), "image/png")}, data={"label": "Image"})


def test_text_source_persists_complete_analysis(source_api, analysis_payload):
    api = source_api
    response = create(api)
    assert response.status_code == 201
    detail = response.json()
    source = detail["source"]
    UUID(source["id"])
    assert source["source_type"] == "text"
    assert source["label"] == "Текст"
    assert source["storage_path"] is None
    snapshot, = detail["snapshots"]
    assert snapshot["extracted_text"] == "Автоматизация для команд"
    analysis, = detail["analyses"]
    assert analysis["snapshot_id"] == snapshot["id"]
    assert analysis["analysis_type"] == "source"
    assert analysis["model_id"] == settings.ai_model
    assert analysis["prompt_version"] == ANALYSIS_PROMPT_VERSION
    assert analysis["result_json"] == analysis_payload
    assert analysis["duration_ms"] >= 0
    prepared = api.boundary.await_args.args[0]
    assert prepared.competitor_name == "Alpha"
    assert prepared.text_context == snapshot["extracted_text"]
    assert prepared.image_inputs == []
    assert prepared.origin_metadata["snapshot_id"] == snapshot["id"]
    with api.sessions() as db:
        stored = db.get(Analysis, analysis["id"])
        assert CompetitorAnalysis.model_validate_json(stored.result_json).model_dump(mode="json") == analysis_payload
    assert api.client.get(f"/api/v2/sources/{source['id']}").json() == detail
    assert api.client.delete(f"/api/v2/sources/{source['id']}").status_code == 204
    assert_empty(api)


@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")])
def test_image_upload_original_bytes_and_safe_path(source_api, format, mime):
    api = source_api
    content = image_bytes(format)
    original = "..\\..\\outside.png"
    response = api.client.post(
        f"/api/v2/competitors/{api.competitor_id}/sources/file",
        files={"file": (original, content, mime)},
    )
    assert response.status_code == 201
    detail = response.json()
    source = detail["source"]
    assert source["original_filename"] == original
    path = api.uploads / source["storage_path"]
    UUID(path.stem)
    assert path.resolve().parent == api.uploads.resolve()
    assert path.read_bytes() == content
    assert source["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert source["mime_type"] == mime
    metadata = detail["snapshots"][0]["metadata_json"]
    assert (metadata["width"], metadata["height"]) == (4, 3)
    assert metadata["storage_path"] == source["storage_path"]
    prepared = api.boundary.await_args.args[0]
    assert prepared.source_type == "image"
    header, encoded = prepared.image_inputs[0].split(",", 1)
    assert header == f"data:{mime};base64"
    assert base64.b64decode(encoded) == content
    with api.sessions() as db:
        for model in (Source, SourceSnapshot, Analysis):
            assert db.query(model).count() == 1
        assert "base64" not in db.query(SourceSnapshot).one().metadata_json
    fetched = api.client.get(f"/api/v2/sources/{source['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == detail
    deleted = api.client.delete(f"/api/v2/sources/{source['id']}")
    assert deleted.status_code == 204 and deleted.content == b""
    assert not path.exists()
    assert api.client.get(f"/api/v2/sources/{source['id']}").status_code == 404
    assert api.client.delete(f"/api/v2/sources/{source['id']}").status_code == 404
    assert_empty(api)


@pytest.mark.parametrize("payload", [{"text": "short"}, {"text": "A" * 30001}, {"text": " " * 10}, {"text": "Content text", "label": "A" * 121}, {"text": "Content text", "unknown": True}])
def test_text_validation(source_api, payload):
    response = source_api.client.post(f"/api/v2/competitors/{source_api.competitor_id}/sources/text", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    source_api.boundary.assert_not_awaited()
    assert_empty(source_api)


def test_configured_text_limit(source_api, monkeypatch):
    monkeypatch.setattr(settings, "max_text_chars", 10)
    response = create(source_api)
    assert response.status_code == 422
    assert_empty(source_api)


def test_missing_competitor_and_source(source_api):
    api = source_api
    for path in ("text", "file"):
        response = api.client.post("/api/v2/competitors/missing/sources/" + path, json={"text": "Valid text content"}) if path == "text" else api.client.post("/api/v2/competitors/missing/sources/file", files={"file": ("a.png", image_bytes(), "image/png")})
        assert response.status_code == 404
    assert api.client.get("/api/v2/sources/missing").status_code == 404
    api.boundary.assert_not_awaited()
    assert_empty(api)


@pytest.mark.parametrize("content,mime", [
    (b"corrupt", "image/png"), (image_bytes(), "image/jpeg"),
    (image_bytes("GIF"), "image/gif"), (b"%PDF-1.7", "application/pdf"),
    (b"text", "text/plain"), (image_bytes("GIF"), "image/png"),
])
def test_invalid_uploads(source_api, content, mime):
    response = source_api.client.post(f"/api/v2/competitors/{source_api.competitor_id}/sources/file", files={"file": ("a.png", content, mime)})
    assert response.status_code == 400
    source_api.boundary.assert_not_awaited()
    assert_empty(source_api)


def test_actual_size_limit_without_content_length_trust(source_api, monkeypatch):
    monkeypatch.setattr(settings, "max_image_mb", 1)
    response = source_api.client.post(f"/api/v2/competitors/{source_api.competitor_id}/sources/file", files={"file": ("a.png", b"A" * (1024 * 1024 + 1), "image/png")})
    assert response.status_code == 413
    source_api.boundary.assert_not_awaited()
    assert_empty(source_api)


@pytest.mark.parametrize("kind", ["text", "image"])
@pytest.mark.parametrize("failure,status", [
    (AIConfigurationError, 500), (AIProviderError, 502),
    (AIProviderTimeoutError, 504), (AIResponseError, 502),
])
def test_ai_failure_preserves_retryable_source(source_api, kind, failure, status):
    source_api.boundary.side_effect = failure("private-provider-details")
    response = create(source_api, kind)
    assert response.status_code == status
    assert "private-provider-details" not in response.text
    assert_retryable(source_api)


@pytest.mark.parametrize("kind", ["text", "image"])
@pytest.mark.parametrize("failure", ["commit", "snapshot"])
def test_db_failure_rolls_back_all_entities(source_api, monkeypatch, kind, failure):
    def fail(*args, **kwargs):
        raise SQLAlchemyError("private-database-details")
    with monkeypatch.context() as patch:
        patch.setattr(Session if failure == "commit" else sources, "commit" if failure == "commit" else "create_snapshot", fail)
        response = create(source_api, kind)
    assert response.status_code == 500
    assert "private-database-details" not in response.text
    source_api.boundary.assert_not_awaited()
    assert_empty(source_api)


def test_storage_failure_is_safe(source_api, monkeypatch):
    monkeypatch.setattr(StorageService, "save", Mock(side_effect=OSError("private-filesystem-details")))
    response = create(source_api, "image")
    assert response.status_code == 500
    assert "private-filesystem-details" not in response.text
    source_api.boundary.assert_not_awaited()
    assert_empty(source_api)


def test_missing_credentials_real_service_no_constructor(source_api, monkeypatch):
    monkeypatch.setattr(settings, "ai_api_key", SecretStr(""))
    monkeypatch.setattr(ai_service, "analyze_source", AIService.analyze_source.__get__(ai_service))
    constructor = Mock(side_effect=AssertionError("Unexpected client"))
    monkeypatch.setattr("backend.services.ai_service.AsyncOpenAI", constructor)
    assert create(source_api).status_code == 500
    assert create(source_api, "image").status_code == 500
    constructor.assert_not_called()
    assert source_api.client.get("/api/v2/health").status_code == 200
    assert source_api.client.get("/api/v2/competitors").status_code == 200
    assert_retryable(source_api, count=2)


@pytest.mark.parametrize("target", ["source", "competitor"])
@pytest.mark.parametrize("failure", ["commit", "file"])
def test_delete_failure_preserves_db_and_file(source_api, monkeypatch, target, failure):
    api = source_api
    detail = create(api, "image").json()
    source_id = detail["source"]["id"]
    path = api.uploads / detail["source"]["storage_path"]
    before = path.read_bytes()
    with monkeypatch.context() as patch:
        if failure == "commit":
            patch.setattr(Session, "commit", Mock(side_effect=SQLAlchemyError("Commit failed")))
        else:
            patch.setattr(StorageService, "remove_reversibly", Mock(side_effect=OSError("Delete failed")))
        if target == "source":
            assert api.client.delete(f"/api/v2/sources/{source_id}").status_code == 500
        else:
            with pytest.raises((SQLAlchemyError, OSError)):
                api.client.delete(f"/api/v2/competitors/{api.competitor_id}")
    assert path.read_bytes() == before
    assert api.client.get(f"/api/v2/sources/{source_id}").json() == detail


def test_delete_competitor_cleans_cascades_and_upload(source_api):
    api = source_api
    create(api)
    create(api, "image")
    response = api.client.delete(f"/api/v2/competitors/{api.competitor_id}")
    assert response.status_code == 204
    assert_empty(api)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["text", "image"])
async def test_cancelled_analysis_preserves_source(source_api, kind):
    api = source_api
    entered = asyncio.Event()

    async def blocked(prepared):
        entered.set()
        await asyncio.Event().wait()

    api.boundary.side_effect = blocked
    with api.sessions() as db:
        operation = ingestion_service.ingest_image(db, api.competitor_id, label="Image", content=image_bytes(), filename="a.png", declared_mime="image/png") if kind == "image" else ingestion_service.ingest_text(db, api.competitor_id, TextSourceCreate(text="Автоматизация для команд"))
        task = asyncio.create_task(operation)
        await asyncio.wait_for(entered.wait(), timeout=5)
        if kind == "image":
            assert list(api.uploads.iterdir())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert_retryable(api)


def test_openapi_source_schemas(source_api):
    schema = source_api.client.get("/openapi.json").json()
    paths = schema["paths"]
    text_route = paths["/api/v2/competitors/{competitor_id}/sources/text"]["post"]
    assert text_route["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/TextSourceCreate")
    for route in (text_route, paths["/api/v2/competitors/{competitor_id}/sources/file"]["post"]):
        assert route["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/SourceDetailResponse")
    assert "multipart/form-data" in paths["/api/v2/competitors/{competitor_id}/sources/file"]["post"]["requestBody"]["content"]
    assert {"get", "delete"} <= paths["/api/v2/sources/{source_id}"].keys()
    assert "post" in paths["/api/v2/sources/{source_id}/reanalyze"]
    assert not any("refresh" in path or path.endswith("/sources/url") or "comparison" in path or "aggregate" in path for path in paths)


def test_invalid_result_not_persisted(source_api):
    source_api.boundary.return_value.scorecard.trust.score = 11
    assert create(source_api, "image").status_code == 502
    assert_retryable(source_api)


def test_competitor_deleted_during_ai_call(source_api, analysis_payload):
    from backend.services.ingestion_service import delete_competitor
    api = source_api

    async def delete_parent(prepared):
        with api.sessions() as db:
            assert delete_competitor(db, api.competitor_id)
        return CompetitorAnalysis.model_validate(analysis_payload)

    api.boundary.side_effect = delete_parent
    assert create(api, "image").status_code == 404
    assert_empty(api)


def test_multiple_file_deletion_failure_restores_previous_file(source_api, monkeypatch):
    api = source_api
    first = create(api, "image").json()
    second = create(api, "image").json()
    original = StorageService.remove_reversibly
    count = 0

    def fail_second(storage, path):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("Injected second file failure")
        return original(storage, path)

    with monkeypatch.context() as patch:
        patch.setattr(StorageService, "remove_reversibly", fail_second)
        with pytest.raises(OSError):
            api.client.delete(f"/api/v2/competitors/{api.competitor_id}")
    for detail in (first, second):
        assert (api.uploads / detail["source"]["storage_path"]).read_bytes() == image_bytes()
        assert api.client.get(f"/api/v2/sources/{detail['source']['id']}").status_code == 200


def test_image_and_text_content_not_logged(source_api, caplog):
    api = source_api
    content_marker = "sensitive-test-only-user-context"
    with caplog.at_level("DEBUG"):
        assert api.client.post(f"/api/v2/competitors/{api.competitor_id}/sources/text", json={"text": content_marker}).status_code == 201
        assert create(api, "image").status_code == 201
    assert content_marker not in caplog.text
    assert api.boundary.await_args.args[0].image_inputs[0] not in caplog.text


@pytest.mark.parametrize("kind", ["text", "image"])
@pytest.mark.parametrize("failure", ["commit", "insert"])
def test_analysis_persistence_failure_preserves_source(source_api, monkeypatch, kind, failure):
    original_commit = Session.commit
    commits = 0

    def fail_second_commit(db):
        nonlocal commits
        commits += 1
        if commits == 2:
            raise SQLAlchemyError("Injected analysis commit failure")
        return original_commit(db)

    with monkeypatch.context() as patch:
        if failure == "commit":
            patch.setattr(Session, "commit", fail_second_commit)
        else:
            patch.setattr(analyses, "create_analysis", Mock(side_effect=SQLAlchemyError("Injected analysis insert failure")))
        response = create(source_api, kind)
    assert response.status_code == 500
    assert_retryable(source_api)


@pytest.mark.parametrize("kind", ["text", "image"])
def test_reanalyze_appends_to_same_snapshot(source_api, kind, monkeypatch):
    api = source_api
    initial = create(api, kind).json()
    source_id = initial["source"]["id"]
    snapshot_id = initial["snapshots"][0]["id"]
    file_reads = []
    original_read = StorageService.read

    def track_read(storage, path, max_bytes):
        file_reads.append(path)
        return original_read(storage, path, max_bytes)

    monkeypatch.setattr(StorageService, "read", track_read)
    uploads_before = {path.name: path.read_bytes() for path in api.uploads.iterdir()}
    response = api.client.post(f"/api/v2/sources/{source_id}/reanalyze")
    assert response.status_code == 201
    detail = response.json()
    assert detail["source"] == initial["source"]
    assert detail["snapshots"] == initial["snapshots"]
    assert len(detail["analyses"]) == 2
    assert len({analysis["id"] for analysis in detail["analyses"]}) == 2
    assert {analysis["snapshot_id"] for analysis in detail["analyses"]} == {snapshot_id}
    with api.sessions() as db:
        assert db.query(Source).count() == 1
        assert db.query(SourceSnapshot).count() == 1
        assert db.query(Analysis).count() == 2
    prepared = api.boundary.await_args.args[0]
    assert prepared.origin_metadata["snapshot_id"] == snapshot_id
    if kind == "image":
        assert file_reads == [initial["source"]["storage_path"]]
        assert base64.b64decode(prepared.image_inputs[0].split(",", 1)[1]) == image_bytes()
    else:
        assert prepared.text_context == initial["snapshots"][0]["extracted_text"]
        assert prepared.image_inputs == []
    assert {path.name: path.read_bytes() for path in api.uploads.iterdir()} == uploads_before


@pytest.mark.parametrize("kind", ["text", "image"])
@pytest.mark.parametrize("failure,status", [(AIProviderError, 502), (AIProviderTimeoutError, 504), (AIConfigurationError, 500), (AIResponseError, 502)])
def test_failed_reanalyze_preserves_history(source_api, kind, failure, status):
    api = source_api
    initial = create(api, kind).json()
    source_id = initial["source"]["id"]
    uploads_before = {path.name: path.read_bytes() for path in api.uploads.iterdir()}
    api.boundary.side_effect = failure("private-provider-details")
    assert api.client.post(f"/api/v2/sources/{source_id}/reanalyze").status_code == status
    assert api.client.get(f"/api/v2/sources/{source_id}").json() == initial
    assert {path.name: path.read_bytes() for path in api.uploads.iterdir()} == uploads_before


@pytest.mark.parametrize("problem", ["missing", "unsafe_path", "changed_bytes"])
def test_image_reanalyze_storage_failure(source_api, problem):
    api = source_api
    initial = create(api, "image").json()
    source_id = initial["source"]["id"]
    path = api.uploads / initial["source"]["storage_path"]
    if problem == "missing":
        path.unlink()
    elif problem == "changed_bytes":
        path.write_bytes(image_bytes("JPEG"))
    else:
        with api.sessions() as db:
            db.get(Source, source_id).storage_path = "../outside.png"
            db.commit()
    api.boundary.reset_mock()
    response = api.client.post(f"/api/v2/sources/{source_id}/reanalyze")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    api.boundary.assert_not_awaited()
    with api.sessions() as db:
        assert db.query(Source).count() == 1
        assert db.query(SourceSnapshot).count() == 1
        assert db.query(Analysis).count() == 1


def test_reanalyze_unknown_source(source_api):
    assert source_api.client.post("/api/v2/sources/missing/reanalyze").status_code == 404
    source_api.boundary.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_before_first_commit_cleans_upload(source_api, monkeypatch):
    from threading import Event
    api = source_api
    entered, release = Event(), Event()
    original_snapshot = sources.create_snapshot

    def blocked_snapshot(*args, **kwargs):
        snapshot = original_snapshot(*args, **kwargs)
        entered.set()
        assert release.wait(timeout=5)
        return snapshot

    monkeypatch.setattr(sources, "create_snapshot", blocked_snapshot)
    with api.sessions() as db:
        task = asyncio.create_task(ingestion_service.ingest_image(db, api.competitor_id, label="Image", content=image_bytes(), filename="a.png", declared_mime="image/png"))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    api.boundary.assert_not_awaited()
    assert_empty(api)


@pytest.mark.parametrize("kind", ["text", "image"])
def test_source_and_snapshot_committed_before_ai(source_api, analysis_payload, kind):
    api = source_api

    async def inspect_initial_persistence(prepared):
        with api.sessions() as db:
            assert db.query(Source).count() == 1
            assert db.query(SourceSnapshot).count() == 1
            assert db.query(Analysis).count() == 0
            snapshot = db.query(SourceSnapshot).one()
            assert snapshot.id == prepared.origin_metadata["snapshot_id"]
        if kind == "image":
            assert list(api.uploads.iterdir())
        return CompetitorAnalysis.model_validate(analysis_payload)

    api.boundary.side_effect = inspect_initial_persistence
    assert create(api, kind).status_code == 201


@pytest.mark.asyncio
async def test_cancelled_reanalyze_preserves_old_analysis(source_api):
    api = source_api
    initial = create(api, "image").json()
    source_id = initial["source"]["id"]
    entered = asyncio.Event()

    async def blocked(prepared):
        entered.set()
        await asyncio.Event().wait()

    api.boundary.side_effect = blocked
    with api.sessions() as db:
        task = asyncio.create_task(ingestion_service.reanalyze(db, source_id))
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert api.client.get(f"/api/v2/sources/{source_id}").json() == initial
    assert (api.uploads / initial["source"]["storage_path"]).read_bytes() == image_bytes()
