"""Cross-type deletion, secondary artifacts and rollback protection."""
from uuid import uuid4
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from backend.models.db import Competitor, Source, SourceSnapshot, Analysis
from backend.services.storage_service import ScreenshotStorage
from test_sources import source_api, create
from test_url_sources import url_api, upload
from test_documents import pdf_bytes


@pytest.mark.parametrize("competitor_delete", [False, True])
@pytest.mark.parametrize("rollback", [False, True])
def test_mixed_artifact_cleanup_and_unrelated_file_protection(url_api, monkeypatch, competitor_delete, rollback):
    api = url_api
    details = [create(api).json(), create(api, "image").json(),
        api.client.post(f"/api/v2/competitors/{api.competitor_id}/sources/file",
                        files={"file": ("original.pdf", pdf_bytes(), "application/pdf")}).json(), upload(api).json()]
    url_source = details[-1]["source"]["id"]
    assert api.client.post(f"/api/v2/sources/{url_source}/refresh").status_code == 201
    storage = ScreenshotStorage(api.screenshots)
    with api.sessions() as db:
        for snapshot in db.query(SourceSnapshot).filter_by(source_id=url_source):
            snapshot.secondary_screenshot_path = storage.save(b"secondary-owned-artifact", ".png").storage_path
        db.commit()
    untouched = []
    for directory in (api.uploads, api.screenshots):
        path = directory / f"{uuid4()}.png"
        path.write_bytes(b"unrelated-file")
        untouched.append(path)
    before = {path: path.read_bytes() for directory in (api.uploads, api.screenshots) for path in directory.iterdir()}
    if rollback:
        def fail_commit(db):
            raise SQLAlchemyError("injected delete rollback")
        monkeypatch.setattr(Session, "commit", fail_commit)
    if competitor_delete:
        responses = [api.client.delete(f"/api/v2/competitors/{api.competitor_id}")]
    else:
        responses = [api.client.delete(f"/api/v2/sources/{detail['source']['id']}") for detail in details]
    assert all(response.status_code == (500 if rollback else 204) for response in responses)
    after = {path: path.read_bytes() for directory in (api.uploads, api.screenshots) for path in directory.iterdir()}
    assert after == (before if rollback else {path: b"unrelated-file" for path in untouched})
    with api.sessions() as db:
        assert db.query(Source).count() == (4 if rollback else 0)
        assert db.query(SourceSnapshot).count() == (5 if rollback else 0)
        assert db.query(Analysis).count() == (5 if rollback else 0)
        assert db.query(Competitor).count() == (0 if competitor_delete and not rollback else 1)
