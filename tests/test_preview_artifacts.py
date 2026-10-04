"""Artifact ownership, safe storage resolution and content type regression."""
from uuid import uuid4

import pytest

from backend.models.db import Source, SourceSnapshot
from test_sources import source_api, image_bytes
from test_url_sources import url_api, upload


def artifact_path(detail):
    return f"/api/v2/sources/{detail['source']['id']}/snapshots/{detail['snapshots'][-1]['id']}/artifact"


@pytest.mark.parametrize('format,mime', [('PNG', 'image/png'), ('JPEG', 'image/jpeg'), ('WEBP', 'image/webp')])
def test_image_artifact_exact_bytes_and_no_client_path(source_api, format, mime):
    api = source_api
    content = image_bytes(format)
    detail = api.client.post(f'/api/v2/competitors/{api.competitor_id}/sources/file',
                            files={'file': ('input', content, mime)}).json()
    response = api.client.get(artifact_path(detail))
    assert response.status_code == 200
    assert response.content == content
    assert response.headers['content-type'] == mime
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['cache-control'] == 'no-store'
    assert api.client.get(artifact_path(detail).replace(detail['source']['id'], str(uuid4()))).status_code == 404
    other = api.client.post(f'/api/v2/competitors/{api.competitor_id}/sources/text', json={'text': 'Enough source content'}).json()
    assert api.client.get(artifact_path(detail).replace(detail['snapshots'][0]['id'], other['snapshots'][0]['id'])).status_code == 404
    assert api.client.get(artifact_path(other)).status_code == 404
    (api.uploads / detail['source']['storage_path']).unlink()
    assert api.client.get(artifact_path(detail)).status_code == 404


@pytest.mark.parametrize('unsafe', ['../secret.png', '..\\secret.png', 'C:\\secret.png', 'wrong.png'])
def test_corrupt_db_path_never_served(source_api, unsafe):
    api = source_api
    detail = api.client.post(f'/api/v2/competitors/{api.competitor_id}/sources/file',
                            files={'file': ('image.png', image_bytes(), 'image/png')}).json()
    with api.sessions() as db:
        db.get(Source, detail['source']['id']).storage_path = unsafe
        db.commit()
    assert api.client.get(artifact_path(detail)).status_code == 404


def test_url_artifact_is_persisted_snapshot_not_new_capture(url_api):
    api = url_api
    detail = upload(api).json()
    api.capture.reset_mock()
    response = api.client.get(artifact_path(detail))
    assert response.status_code == 200
    assert response.content == image_bytes()
    api.capture.assert_not_awaited()
    with api.sessions() as db:
        db.get(SourceSnapshot, detail['snapshots'][0]['id']).screenshot_path = '../escape.png'
        db.commit()
    assert api.client.get(artifact_path(detail)).status_code == 404
