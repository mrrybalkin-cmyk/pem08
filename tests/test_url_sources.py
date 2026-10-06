import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models.db import Source, SourceSnapshot, Analysis
from backend.services.browser_service import BrowserCapture, BrowserTimeoutError, BrowserCaptureError, browser_service
from backend.services.ai_service import AIProviderError
from backend.services.ingestion_service import ingestion_service
from backend.models.api import UrlSourceCreate
from test_sources import source_api, image_bytes, create


@pytest.fixture
def url_api(source_api, tmp_path, monkeypatch):
    api = source_api
    monkeypatch.setattr(settings, "screenshot_dir", tmp_path / "screenshots")
    capture = BrowserCapture("https://public.example/", "https://public.example/final", "Title", "Description",
                             "Visible website content", image_bytes(), {"text_truncated": False, "requested_url": "https://public.example/"})
    api.capture = AsyncMock(return_value=capture)
    monkeypatch.setattr(browser_service, "capture", api.capture)
    from backend.security.url_validation import resolve_and_validate_url
    from backend.services import ingestion_service as ingestion_module
    async def validate(url):
        async def resolver(hostname, _port):
            return ('10.0.0.1',) if hostname == 'private-dns.example' else ('93.184.216.34',)
        return await resolve_and_validate_url(url, resolver=resolver)
    monkeypatch.setattr(ingestion_module, 'resolve_and_validate_url', validate)
    api.screenshots = settings.screenshot_dir
    return api


def upload(api):
    return api.client.post(f"/api/v2/competitors/{api.competitor_id}/sources/url", json={"url": "https://public.example"})


def counts(api):
    with api.sessions() as db:
        return tuple(db.query(model).count() for model in (Source, SourceSnapshot, Analysis))


def test_source_is_committed_before_capture_starts(url_api):
    api = url_api
    capture = api.capture.return_value
    observed = []
    async def inspect(url):
        assert counts(api) == (1, 0, 0)
        with api.sessions() as db:
            saved = db.query(Source).one()
            assert saved.url == url == 'https://public.example/'
            observed.append(saved.id)
        api.boundary.assert_not_awaited()
        return capture
    api.capture.side_effect = inspect
    response = upload(api)
    assert response.status_code == 201
    assert response.json()['source']['id'] == observed[0]
    assert response.json()['processing_status'] == 'ready'
    assert counts(api) == (1, 1, 1)


def test_url_create_refresh_reanalyze_and_all_snapshot_cleanup(url_api):
    api = url_api
    response = upload(api)
    assert response.status_code == 201, response.text
    detail = response.json()
    source, snapshot = detail["source"], detail["snapshots"][0]
    assert source["source_type"] == "url" and source["label"] == "Сайт"
    assert source["url"] == "https://public.example/"
    assert source["storage_path"] is None
    assert snapshot["final_url"] == "https://public.example/final"
    assert snapshot["title"] == "Title" and snapshot["meta_description"] == "Description"
    assert snapshot["secondary_screenshot_path"] is None
    prepared = api.boundary.await_args.args[0]
    assert prepared.source_type == "url"
    assert prepared.text_context == "Visible website content"
    assert prepared.image_inputs[0].startswith("data:image/png;base64,")
    assert prepared.origin_metadata["captured_at"]
    assert "base64" not in str(snapshot["metadata_json"])
    sid = source["id"]
    for index in (2, 3):
        response = api.client.post(f"/api/v2/sources/{sid}/refresh")
        assert response.status_code == 201
        assert counts(api) == (1, index, index)
    before = response.json()
    assert len({s["screenshot_path"] for s in before["snapshots"]}) == 3
    api.capture.reset_mock()
    after = api.client.post(f"/api/v2/sources/{sid}/reanalyze").json()
    assert after["snapshots"] == before["snapshots"]
    assert counts(api) == (1, 3, 4)
    api.capture.assert_not_awaited()
    prepared = api.boundary.await_args.args[0]
    assert prepared.origin_metadata["source_id"] == sid
    assert prepared.origin_metadata["snapshot_id"] == before["snapshots"][-1]["id"]
    assert api.client.delete(f"/api/v2/sources/{sid}").status_code == 204
    assert counts(api) == (0, 0, 0)
    assert not list(api.screenshots.glob("*"))


@pytest.mark.parametrize("refresh", [False, True])
def test_url_ai_failure_retry_without_browser(url_api, refresh):
    api = url_api
    old = upload(api).json() if refresh else None
    valid = api.boundary.return_value
    api.boundary.side_effect = AIProviderError("injected")
    response = api.client.post(f"/api/v2/sources/{old['source']['id']}/refresh") if refresh else upload(api)
    assert response.status_code == 502
    assert counts(api) == (1, 2 if refresh else 1, 1 if refresh else 0)
    assert len(list(api.screenshots.glob("*"))) == (2 if refresh else 1)
    with api.sessions() as db:
        sid = db.query(Source).one().id
    api.boundary.side_effect = None
    api.boundary.return_value = valid
    api.capture.reset_mock()
    assert api.client.post(f"/api/v2/sources/{sid}/reanalyze").status_code == 201
    api.capture.assert_not_awaited()
    assert counts(api) == (1, 2 if refresh else 1, 2 if refresh else 1)


@pytest.mark.parametrize("refresh,commit_number", [(False, 1), (False, 2), (False, 3), (True, 1), (True, 2)])
def test_url_commit_failure_cleanup_and_retryable_state(url_api, monkeypatch, refresh, commit_number):
    api = url_api
    detail = upload(api).json() if refresh else None
    original = Session.commit
    commits = 0
    def commit(db):
        nonlocal commits
        commits += 1
        if commits == commit_number:
            raise SQLAlchemyError("injected")
        original(db)
    monkeypatch.setattr(Session, "commit", commit)
    response = api.client.post(f"/api/v2/sources/{detail['source']['id']}/refresh") if refresh else upload(api)
    assert response.status_code == 500
    snapshot_count = commit_number if refresh else max(0, commit_number - 2)
    assert counts(api) == (1 if refresh or commit_number > 1 else 0, snapshot_count, int(refresh))
    assert len(list(api.screenshots.glob("*"))) == snapshot_count
    if not refresh and commit_number == 1:
        api.capture.assert_not_awaited()
    if refresh and commit_number == 1:
        assert api.client.get(f"/api/v2/sources/{detail['source']['id']}").json() == detail


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("failure,status", [(BrowserTimeoutError("timeout"), 504), (BrowserCaptureError("failed"), 500)])
def test_capture_failure_preserves_old_state(url_api, refresh, failure, status):
    api = url_api
    detail = upload(api).json() if refresh else None
    api.capture.side_effect = failure
    response = api.client.post(f"/api/v2/sources/{detail['source']['id']}/refresh") if refresh else upload(api)
    assert response.status_code == 201
    result = response.json()
    assert result['processing_error']['code'] == ('BROWSER_TIMEOUT' if status == 504 else 'BROWSER_ERROR')
    assert result['processing_status'] == ('ready' if refresh else 'capture_failed')
    assert counts(api) == ((1, 1, 1) if refresh else (1, 0, 0))
    assert len(list(api.screenshots.glob("*"))) == (1 if refresh else 0)


@pytest.mark.parametrize("failure,status,code", [
    (BrowserCaptureError("HTTP 403"), 500, "BROWSER_ERROR"),
    (BrowserTimeoutError("timeout"), 504, "BROWSER_TIMEOUT"),
])
def test_initial_perplexity_capture_failure_then_retry(url_api, failure, status, code):
    from backend.models.db import Competitor
    api = url_api
    url = "https://www.perplexity.ai/"
    created = api.client.post('/api/v2/competitors', json={'name': 'Perplexity AI', 'website_url': url})
    assert created.status_code == 201
    cid = created.json()['id']
    path = f'/api/v2/competitors/{cid}/sources/url'
    api.capture.side_effect = failure
    failed = api.client.post(path, json={'url': url})
    assert failed.status_code == 201 and failed.json()['processing_error']['code'] == code
    assert failed.json()['processing_status'] == 'capture_failed'
    sid = failed.json()['source']['id']
    assert counts(api) == (1, 0, 0)
    api.boundary.assert_not_awaited()
    detail = api.client.get(f'/api/v2/competitors/{cid}').json()
    assert detail['website_url'] == url and detail['sources'][0]['id'] == sid
    with api.sessions() as db:
        assert db.query(Competitor).filter_by(name='Perplexity AI').count() == 1
    api.capture.side_effect = None
    api.capture.return_value = BrowserCapture(url, url, 'Perplexity', None, 'Mocked public page', image_bytes(), {})
    retried = api.client.post(f'/api/v2/sources/{sid}/refresh')
    assert retried.status_code == 201
    assert retried.json()['source']['id'] == sid and retried.json()['processing_status'] == 'ready'
    assert counts(api) == (1, 1, 1)
    assert len(api.client.get(f'/api/v2/competitors/{cid}').json()['sources']) == 1


@pytest.mark.parametrize("failure", ["missing", "corrupt", "unsafe"])
def test_url_reanalyze_bad_screenshot(url_api, failure):
    api = url_api
    detail = upload(api).json()
    snapshot = detail["snapshots"][0]
    path = api.screenshots / snapshot["screenshot_path"]
    if failure == "missing":
        path.unlink()
    elif failure == "corrupt":
        path.write_bytes(b"not png")
    else:
        with api.sessions() as db:
            db.get(SourceSnapshot, snapshot["id"]).screenshot_path = "../outside.png"
            db.commit()
    api.boundary.reset_mock()
    assert api.client.post(f"/api/v2/sources/{detail['source']['id']}/reanalyze").status_code == 500
    api.boundary.assert_not_awaited()
    assert counts(api) == (1, 1, 1)


def test_delete_rollback_restores_all_screenshots_and_competitor_cleanup(url_api, monkeypatch):
    api = url_api
    detail = upload(api).json()
    sid = detail["source"]["id"]
    api.client.post(f"/api/v2/sources/{sid}/refresh")
    originals = {path.name: path.read_bytes() for path in api.screenshots.glob("*")}
    with monkeypatch.context() as patch:
        def fail(db):
            raise SQLAlchemyError("injected")
        patch.setattr(Session, "commit", fail)
        assert api.client.delete(f"/api/v2/sources/{sid}").status_code == 500
    assert {path.name: path.read_bytes() for path in api.screenshots.glob("*")} == originals
    assert counts(api) == (1, 2, 2)
    create(api, "image")
    assert api.client.delete(f"/api/v2/competitors/{api.competitor_id}").status_code == 204
    assert not list(api.screenshots.glob("*")) and not list(api.uploads.glob("*"))


def test_url_errors_and_non_url_refresh(url_api):
    api = url_api
    root = f"/api/v2/competitors/{api.competitor_id}/sources/url"
    for url, code in [("http://127.0.0.1", "URL_BLOCKED_PRIVATE_NETWORK"),
                      ("https://localhost", "URL_BLOCKED_LOCAL_HOST"),
                      ("https://user:pass@public.example", "URL_CREDENTIALS_NOT_ALLOWED")]:
        response = api.client.post(root, json={"url": url})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == code
    api.capture.assert_not_awaited()
    for kind in ("text", "image", "pdf"):
        if kind == "pdf":
            from test_documents import pdf_bytes
            detail = api.client.post(f"/api/v2/competitors/{api.competitor_id}/sources/file",
                                     files={"file": ("source.pdf", pdf_bytes(), "application/pdf")}).json()
        else:
            detail = create(api, kind).json()
        sid = detail["source"]["id"]
        assert api.client.post(f"/api/v2/sources/{sid}/refresh").status_code == 400
    assert api.client.post("/api/v2/sources/missing/refresh").status_code == 404
    assert api.client.post(root, json={"url": "invalid"}).status_code == 422


@pytest.mark.parametrize("kind", ["text", "image", "pdf", "url"])
def test_manual_reanalyze_preserves_real_source_and_snapshot_identity(url_api, kind):
    api = url_api
    if kind == "url":
        response = upload(api)
    elif kind == "pdf":
        from test_documents import pdf_bytes
        response = api.client.post(f"/api/v2/competitors/{api.competitor_id}/sources/file",
                                   files={"file": ("source.pdf", pdf_bytes(), "application/pdf")})
    else:
        response = create(api, kind)
    assert response.status_code == 201, response.text
    detail = response.json()
    sid = detail["source"]["id"]
    snapshot_id = detail["snapshots"][-1]["id"]
    api.capture.reset_mock()
    response = api.client.post(f"/api/v2/sources/{sid}/reanalyze")
    assert response.status_code == 201, response.text
    prepared = api.boundary.await_args.args[0]
    assert prepared.origin_metadata["source_id"] == sid
    assert prepared.origin_metadata["snapshot_id"] == snapshot_id
    assert response.json()["snapshots"] == detail["snapshots"]
    assert counts(api) == (1, 1, 2)
    api.capture.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("during", ["capture", "ai"])
async def test_url_cancellation_semantics(url_api, during):
    api = url_api
    async def cancel(*args):
        raise asyncio.CancelledError()
    (api.capture if during == "capture" else api.boundary).side_effect = cancel
    with api.sessions() as db:
        with pytest.raises(asyncio.CancelledError):
            await ingestion_service.ingest_url(db, api.competitor_id, UrlSourceCreate(url="https://public.example"))
    assert counts(api) == ((1, 0, 0) if during == "capture" else (1, 1, 0))
    assert len(list(api.screenshots.glob("*"))) == (0 if during == "capture" else 1)


def test_url_screenshot_write_failure_preserves_source(url_api, monkeypatch):
    from backend.services.storage_service import ScreenshotStorage
    def fail(*args):
        raise OSError("injected write failure")
    monkeypatch.setattr(ScreenshotStorage, "save", fail)
    assert upload(url_api).status_code == 500
    assert counts(url_api) == (1, 0, 0)
    url_api.boundary.assert_not_awaited()
    assert not list(url_api.screenshots.glob("*"))


@pytest.mark.asyncio
async def test_url_cancel_before_snapshot_commit_preserves_source(url_api, monkeypatch):
    from threading import Event
    from backend.repositories import sources
    entered, release = Event(), Event()
    original = sources.create_snapshot
    def paused(db, **kwargs):
        value = original(db, **kwargs)
        entered.set()
        assert release.wait(5)
        return value
    monkeypatch.setattr(sources, "create_snapshot", paused)
    with url_api.sessions() as db:
        task = asyncio.create_task(ingestion_service.ingest_url(db, url_api.competitor_id,
                                                              UrlSourceCreate(url="https://public.example")))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert counts(url_api) == (1, 0, 0)
    assert not list(url_api.screenshots.glob("*"))
    url_api.boundary.assert_not_awaited()


def test_failed_url_survives_fresh_application_and_deletes_normally(url_api):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from backend.api.sources import router as source_router
    from backend.api.competitors import router as competitor_router
    from backend.database import get_db
    api = url_api
    api.capture.side_effect = BrowserCaptureError('HTTP 403')
    result = upload(api).json()
    sid = result['source']['id']
    with api.sessions() as db:
        database_url = db.get_bind().url
    engine = create_engine(database_url)
    try:
        # Fresh application + fresh engine: no original session or UI/service cache.
        restarted_app = FastAPI()
        restarted_app.include_router(source_router)
        restarted_app.include_router(competitor_router)
        def reopened_db():
            with Session(engine) as db:
                yield db
        restarted_app.dependency_overrides[get_db] = reopened_db
        with TestClient(restarted_app) as restarted:
            restored = restarted.get(f'/api/v2/sources/{sid}').json()
            assert restored['processing_status'] == 'capture_failed'
            assert restored['source']['url'] == 'https://public.example/'
            assert restored['snapshots'] == [] and restored['analyses'] == []
            assert restarted.get(f'/api/v2/competitors/{api.competitor_id}').json()['sources'][0]['id'] == sid
    finally:
        engine.dispose()
    assert api.client.delete(f'/api/v2/sources/{sid}').status_code == 204
    assert api.client.get(f'/api/v2/sources/{sid}').status_code == 404
    assert counts(api) == (0, 0, 0)


def test_dns_ssrf_rejected_before_source_persistence(url_api):
    api = url_api
    response = api.client.post(f'/api/v2/competitors/{api.competitor_id}/sources/url', json={'url': 'https://private-dns.example/'})
    assert response.status_code == 400 and response.json()['error']['code'] == 'URL_BLOCKED_PRIVATE_NETWORK'
    assert counts(api) == (0, 0, 0)
    api.capture.assert_not_awaited()
    api.boundary.assert_not_awaited()


def test_aggregate_omits_failed_url_evidence(url_api, monkeypatch):
    from backend.services.ai_service import ai_service
    api = url_api
    aggregate = AsyncMock(return_value=api.boundary.return_value)
    monkeypatch.setattr(ai_service, 'aggregate_competitor', aggregate)
    api.capture.side_effect = BrowserCaptureError('HTTP 403')
    failed = upload(api).json()['source']['id']
    root = f'/api/v2/competitors/{api.competitor_id}'
    assert api.client.post(root + '/aggregate-analysis').status_code == 400
    aggregate.assert_not_awaited()
    ready = api.client.post(root + '/sources/text', json={'text': 'Successfully analyzed evidence.'}).json()['source']['id']
    assert api.client.post(root + '/aggregate-analysis').status_code == 201
    prepared = aggregate.await_args.args[0]
    assert [item.source_id for item in prepared.sources] == [ready]
    assert prepared.coverage.omitted_source_ids == (failed,)
