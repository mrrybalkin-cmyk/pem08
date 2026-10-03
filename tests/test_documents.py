import asyncio
import base64
import hashlib
from io import BytesIO
from uuid import UUID

import pymupdf
import pytest
from PIL import Image
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models.db import Source, SourceSnapshot, Analysis
from backend.services.document_service import InvalidPDF, prepare_pdf, select_pages
from backend.services.ai_service import AIConfigurationError, AIProviderError, AIProviderTimeoutError, AIResponseError
from backend.services.ingestion_service import ingestion_service
from test_sources import source_api, image_bytes, create


def pdf_bytes(count=1, kind="text", encrypted=False, user_password="password"):
    with pymupdf.open() as document:
        for index in range(count):
            page = document.new_page(width=200, height=240)
            if kind == "text" or (kind == "mixed" and index % 2 == 0):
                page.insert_text((15, 30), f"Page content {index + 1}: Automation for teams")
            else:
                stream = BytesIO()
                image = Image.new("RGB", (100, 120), "blue")
                from PIL import ImageDraw
                ImageDraw.Draw(image).text((5, 5), "Visible offer", fill="white")
                image.save(stream, format="PNG")
                page.insert_image(page.rect, stream=stream.getvalue())
        options = dict(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw=user_password) if encrypted else {}
        return document.tobytes(**options)


def upload(api, content=None, mime="application/pdf", filename="..\\outside.pdf"):
    return api.client.post(f"/api/v2/competitors/{api.competitor_id}/sources/file",
                          files={"file": (filename, pdf_bytes() if content is None else content, mime)})


@pytest.mark.parametrize("count", [1, 2, 7, 8, 9, 10, 20, 100])
def test_selection(count):
    pages = select_pages(count, 8)
    assert pages == select_pages(count, 8)
    assert pages == sorted(set(pages))
    assert pages[0] == 1 and len(pages) == min(count, 8)
    if count <= 8:
        assert pages == list(range(1, count + 1))
    else:
        assert pages[-1] == count
        gaps = [right - left for left, right in zip(pages, pages[1:])]
        assert max(gaps) - min(gaps) <= 1


def test_selection_limit_one():
    assert select_pages(100, 1) == [1]
    with pytest.raises(InvalidPDF):
        select_pages(0, 8)


@pytest.mark.parametrize("kind", ["text", "scanned", "mixed"])
def test_document_preparation(kind):
    content = pdf_bytes(2, kind)
    prepared = prepare_pdf(content, "application/pdf", len(content), 8, 30000)
    assert prepared.metadata["selected_pages"] == [1, 2]
    assert prepared.metadata["partial_analysis"] is False
    assert prepared.metadata["text_page_count"] == {"text": 2, "scanned": 0, "mixed": 1}[kind]
    assert len(prepared.image_inputs) == 2
    if kind == "scanned":
        with pymupdf.open(stream=content, filetype="pdf") as document:
            assert all(not page.get_text().strip() for page in document)
    else:
        assert "Page content 1" in prepared.extracted_text
    for url in prepared.image_inputs:
        image = Image.open(BytesIO(base64.b64decode(url.split(",")[1])))
        assert max(image.size) <= 1600


def test_text_budget_and_render_bound():
    prepared = prepare_pdf(pdf_bytes(2), "application/pdf", 100000, 8, 20)
    assert len(prepared.extracted_text) == 20
    assert prepared.metadata["text_truncated"]
    with pymupdf.open() as document:
        document.new_page(width=10000, height=10000)
        content = document.tobytes()
    result = prepare_pdf(content, "application/pdf", 100000, 8, 30000)
    image = Image.open(BytesIO(base64.b64decode(result.image_inputs[0].split(",")[1])))
    assert max(image.size) <= 1600


@pytest.mark.parametrize("kind,count", [("text", 1), ("text", 20), ("scanned", 1), ("mixed", 2)])
def test_pdf_end_to_end_and_reanalyze(source_api, kind, count):
    api = source_api
    content = pdf_bytes(count, kind)
    response = upload(api, content)
    assert response.status_code == 201, response.text
    detail = response.json()
    source, snapshot = detail["source"], detail["snapshots"][0]
    assert source["source_type"] == "pdf"
    assert source["original_filename"] == "..\\outside.pdf"
    assert source["mime_type"] == "application/pdf"
    path = api.uploads / source["storage_path"]
    assert path.suffix == ".pdf"
    UUID(path.stem)
    assert path.resolve().parent == api.uploads.resolve()
    assert path.read_bytes() == content
    assert source["sha256"] == hashlib.sha256(content).hexdigest()
    metadata = snapshot["metadata_json"]
    assert metadata["page_count"] == count
    assert metadata["selected_pages"] == select_pages(count, 8)
    prepared = api.boundary.await_args.args[0]
    assert prepared.source_type == "pdf"
    assert prepared.text_context == snapshot["extracted_text"]
    assert len(prepared.image_inputs) == min(count, 8)
    limitations = detail["analyses"][0]["result_json"]["limitations"]
    assert any("страниц PDF" in item for item in limitations) == (count > 8)
    sid = source["id"]
    # Persisted selection remains authoritative even if current config changes.
    response = api.client.post(f"/api/v2/sources/{sid}/reanalyze")
    assert response.status_code == 201
    again = response.json()
    assert again["source"] == source and again["snapshots"] == [snapshot]
    assert len(again["analyses"]) == 2
    assert again["analyses"][0]["id"] != detail["analyses"][0]["id"]
    assert path.read_bytes() == content
    assert list(api.uploads.iterdir()) == [path]
    assert api.boundary.await_args.args[0].image_inputs == prepared.image_inputs
    with api.sessions() as db:
        assert db.query(SourceSnapshot).count() == 1
        assert db.query(Analysis).count() == 2
        from backend.models.analysis import CompetitorAnalysis
        CompetitorAnalysis.model_validate_json(db.query(Analysis).first().result_json)
    assert len(api.client.get(f"/api/v2/competitors/{api.competitor_id}/analyses").json()) == 2
    assert api.client.get(f"/api/v2/competitors/{api.competitor_id}").json()["sources"][0] == source


@pytest.mark.parametrize("case", ["corrupt", "truncated", "encrypted", "image", "pdf_as_image"])
def test_invalid_pdf_upload(source_api, case):
    content, mime = {
        "corrupt": (b"%PDF-1.7\nbroken", "application/pdf"),
        "truncated": (pdf_bytes()[:150], "application/pdf"),
        "encrypted": (pdf_bytes(encrypted=True), "application/pdf"),
        "image": (image_bytes(), "application/pdf"),
        "pdf_as_image": (pdf_bytes(), "image/png"),
    }[case]
    response = upload(source_api, content, mime)
    assert response.status_code == 400
    assert "error" in response.json()
    source_api.boundary.assert_not_awaited()
    with source_api.sessions() as db:
        assert db.query(Source).count() == 0
    assert not list(source_api.uploads.glob("*"))


@pytest.mark.parametrize("user_password", ["password", ""])
def test_encrypted_pdf_rejected_before_persistence(source_api, user_password):
    content = pdf_bytes(encrypted=True, user_password=user_password)
    with pymupdf.open(stream=content, filetype="pdf") as document:
        if user_password:
            assert document.needs_pass
            assert document.metadata is None
        else:
            assert not document.needs_pass
            assert not document.is_encrypted
            assert document.metadata["encryption"]
    response = upload(source_api, content)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PDF"
    source_api.boundary.assert_not_awaited()
    with source_api.sessions() as db:
        assert db.query(Source).count() == 0
        assert db.query(SourceSnapshot).count() == 0
        assert db.query(Analysis).count() == 0
    assert not list(source_api.uploads.glob("*"))


@pytest.mark.parametrize("user_password", ["password", ""])
def test_document_service_rejects_all_encryption(user_password):
    content = pdf_bytes(encrypted=True, user_password=user_password)
    with pytest.raises(InvalidPDF, match="Encrypted PDF"):
        prepare_pdf(content, "application/pdf", len(content), 8, 30000)


@pytest.mark.parametrize("kind", ["image", "pdf"])
@pytest.mark.parametrize("label", [None, "Custom label"])
def test_generic_file_label(source_api, kind, label):
    content, mime = (image_bytes(), "image/png") if kind == "image" else (pdf_bytes(), "application/pdf")
    response = source_api.client.post(
        f"/api/v2/competitors/{source_api.competitor_id}/sources/file",
        files={"file": ("source", content, mime)}, data={} if label is None else {"label": label},
    )
    assert response.status_code == 201
    assert response.json()["source"]["label"] == ("Файл" if label is None else label)


def test_size_and_large_spoofing(source_api, monkeypatch):
    monkeypatch.setattr(settings, "max_image_mb", 1)
    monkeypatch.setattr(settings, "max_pdf_mb", 2)
    # Valid PDF bytes plus padding need not be decoded to reject oversize.
    content = pdf_bytes() + b" " * (2 * 1024 * 1024)
    assert upload(source_api, content).status_code == 413
    assert upload(source_api, content, "image/png").status_code == 400
    assert upload(source_api, image_bytes() + b" " * (1024 * 1024), "application/pdf").status_code == 400
    source_api.boundary.assert_not_awaited()
    assert not list(source_api.uploads.glob("*"))


@pytest.mark.parametrize("error,status", [(AIConfigurationError("config"), 500), (AIProviderError("provider"), 502),
                                         (AIProviderTimeoutError("timeout"), 504), (AIResponseError("invalid"), 502)])
def test_pdf_ai_failure_and_retry(source_api, error, status):
    api = source_api
    valid = api.boundary.return_value
    api.boundary.side_effect = error
    assert upload(api).status_code == status
    with api.sessions() as db:
        source = db.query(Source).one()
        sid, storage_path = source.id, source.storage_path
        assert db.query(SourceSnapshot).count() == 1
        assert db.query(Analysis).count() == 0
    assert (api.uploads / storage_path).read_bytes().startswith(b"%PDF")
    api.boundary.side_effect = None
    api.boundary.return_value = valid
    detail = api.client.post(f"/api/v2/sources/{sid}/reanalyze").json()
    api.boundary.side_effect = error
    assert api.client.post(f"/api/v2/sources/{sid}/reanalyze").status_code == status
    assert api.client.get(f"/api/v2/sources/{sid}").json() == detail
    assert (api.uploads / storage_path).exists()


@pytest.mark.parametrize("failed_commit", [1, 2])
def test_pdf_commit_failure(source_api, monkeypatch, failed_commit):
    api = source_api
    original = Session.commit
    commits = 0
    def fail(db):
        nonlocal commits
        commits += 1
        if commits == failed_commit:
            raise SQLAlchemyError("injected")
        return original(db)
    monkeypatch.setattr(Session, "commit", fail)
    assert upload(api).status_code == 500
    with api.sessions() as db:
        assert db.query(Source).count() == failed_commit - 1
        assert db.query(SourceSnapshot).count() == failed_commit - 1
        assert db.query(Analysis).count() == 0
    assert len(list(api.uploads.glob("*"))) == failed_commit - 1
    assert api.boundary.await_count == failed_commit - 1


@pytest.mark.parametrize("failure", ["missing", "tampered"])
def test_pdf_reanalyze_storage_failure(source_api, failure):
    api = source_api
    detail = upload(api).json()
    path = api.uploads / detail["source"]["storage_path"]
    path.unlink() if failure == "missing" else path.write_bytes(pdf_bytes(2))
    api.boundary.reset_mock()
    assert api.client.post(f"/api/v2/sources/{detail['source']['id']}/reanalyze").status_code == 500
    api.boundary.assert_not_awaited()
    assert api.client.get(f"/api/v2/sources/{detail['source']['id']}").json() == detail


def test_pdf_delete_rollback_and_competitor_cleanup(source_api, monkeypatch):
    api = source_api
    detail = upload(api).json()
    create(api, "image")
    sid = detail["source"]["id"]
    path = api.uploads / detail["source"]["storage_path"]
    content = path.read_bytes()
    with monkeypatch.context() as patch:
        def fail(db):
            raise SQLAlchemyError("injected")
        patch.setattr(Session, "commit", fail)
        assert api.client.delete(f"/api/v2/sources/{sid}").status_code == 500
    assert path.read_bytes() == content
    assert api.client.get(f"/api/v2/sources/{sid}").json() == detail
    assert api.client.delete(f"/api/v2/competitors/{api.competitor_id}").status_code == 204
    assert not list(api.uploads.glob("*"))
    with api.sessions() as db:
        assert db.query(Source).count() == db.query(SourceSnapshot).count() == db.query(Analysis).count() == 0


def test_pdf_delete_success(source_api):
    detail = upload(source_api).json()
    assert source_api.client.delete(f"/api/v2/sources/{detail['source']['id']}").status_code == 204
    assert not list(source_api.uploads.glob("*"))


def test_snapshot_selection_and_budget_survive_config_change(source_api, monkeypatch):
    monkeypatch.setattr(settings, "max_pdf_pages_analyzed", 3)
    monkeypatch.setattr(settings, "max_text_chars", 50)
    detail = upload(source_api, pdf_bytes(10)).json()
    assert detail["snapshots"][0]["metadata_json"]["selected_pages"] == [1, 5, 10]
    assert len(detail["snapshots"][0]["extracted_text"]) == 50
    prepared = source_api.boundary.await_args.args[0]
    monkeypatch.setattr(settings, "max_pdf_pages_analyzed", 1)
    monkeypatch.setattr(settings, "max_text_chars", 10)
    response = source_api.client.post(f"/api/v2/sources/{detail['source']['id']}/reanalyze")
    assert response.status_code == 201
    assert response.json()["snapshots"] == detail["snapshots"]
    assert source_api.boundary.await_args.args[0] == prepared


def test_pdf_larger_than_image_limit_accepted_with_bounded_read(source_api, monkeypatch):
    from starlette.datastructures import UploadFile
    monkeypatch.setattr(settings, "max_image_mb", 1)
    monkeypatch.setattr(settings, "max_pdf_mb", 2)
    original_read = UploadFile.read
    reads = []
    async def read(file, size=-1):
        reads.append(size)
        return await original_read(file, size)
    monkeypatch.setattr(UploadFile, "read", read)
    import random
    with pymupdf.open(stream=pdf_bytes(), filetype="pdf") as document:
        document.embfile_add("fixture.bin", random.Random(0).randbytes(1024 * 1024))
        content = document.tobytes()
    assert 1024 * 1024 < len(content) < 2 * 1024 * 1024
    assert upload(source_api, content).status_code == 201
    assert reads == [2 * 1024 * 1024 + 1]


def test_source_snapshot_committed_before_pdf_ai(source_api):
    api = source_api
    valid = api.boundary.return_value
    async def analyze(prepared):
        with api.sessions() as db:
            assert db.query(Source).count() == db.query(SourceSnapshot).count() == 1
            assert db.query(Analysis).count() == 0
            source = db.query(Source).one()
            assert (api.uploads / source.storage_path).exists()
        return valid
    api.boundary.side_effect = analyze
    assert upload(api).status_code == 201


def test_pdf_storage_failure_has_no_rows(source_api, monkeypatch):
    from backend.services.storage_service import StorageService
    def fail(*args):
        raise OSError("injected")
    monkeypatch.setattr(StorageService, "save", fail)
    assert upload(source_api).status_code == 500
    source_api.boundary.assert_not_awaited()
    with source_api.sessions() as db:
        assert db.query(Source).count() == db.query(SourceSnapshot).count() == db.query(Analysis).count() == 0


def test_pdf_required_limitations_with_full_provider_array(source_api):
    from backend.models.analysis import CompetitorAnalysis
    data = source_api.boundary.return_value.model_dump(mode="json")
    data["limitations"] = [f"Provider limitation {index}" for index in range(8)]
    source_api.boundary.return_value = CompetitorAnalysis.model_validate(data)
    detail = upload(source_api, pdf_bytes(10)).json()
    limitations = detail["analyses"][0]["result_json"]["limitations"]
    assert len(limitations) == 8
    assert "8 из 10 страниц PDF" in limitations[-1]


def test_zero_page_pdf_controlled(source_api):
    # Actual zero-page PDF with a correct xref; not a fake header-only payload.
    parts = [b"%PDF-1.4\n"]
    offsets = []
    for obj in (b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
                b"2 0 obj\n<< /Type /Pages /Count 0 /Kids [] >>\nendobj\n"):
        offsets.append(sum(map(len, parts)))
        parts.append(obj)
    xref = sum(map(len, parts))
    parts.append((f"xref\n0 3\n0000000000 65535 f \n{offsets[0]:010} 00000 n \n{offsets[1]:010} 00000 n \n"
                  f"trailer\n<< /Size 3 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode())
    content = b"".join(parts)
    with pymupdf.open(stream=content, filetype="pdf") as document:
        assert document.page_count == 0
    assert upload(source_api, content).status_code == 400
    source_api.boundary.assert_not_awaited()
    assert not list(source_api.uploads.glob("*"))


@pytest.mark.asyncio
async def test_pdf_ai_cancellation_keeps_committed_source(source_api):
    api = source_api
    async def cancel(prepared):
        raise asyncio.CancelledError()
    api.boundary.side_effect = cancel
    with api.sessions() as db:
        with pytest.raises(asyncio.CancelledError):
            await ingestion_service.ingest_pdf(db, api.competitor_id, label="PDF", content=pdf_bytes(),
                                               filename="a.pdf", declared_mime="application/pdf")
    with api.sessions() as db:
        assert db.query(Source).count() == db.query(SourceSnapshot).count() == 1
        assert db.query(Analysis).count() == 0
    assert len(list(api.uploads.glob("*.pdf"))) == 1
