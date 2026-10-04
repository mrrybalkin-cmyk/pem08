"""Persist retryable sources first, then append validated analyses separately."""

import asyncio
import hashlib
import json
from threading import Event
from time import perf_counter
from uuid import uuid4

from sqlalchemy.orm import Session
from pydantic import ValidationError

from backend.config import settings
from backend.models.analysis import CompetitorAnalysis, PreparedAnalysisInput, SourceType
from backend.models.api import CompetitorDetailResponse, SourceDetailResponse, TextSourceCreate
from backend.repositories import analyses, competitors, sources
from backend.services.ai_service import AIResponseError, ai_service
from backend.services.prompt_service import ANALYSIS_PROMPT_VERSION
from backend.services.browser_service import browser_service
from backend.security.url_validation import InvalidURL, validate_url
from backend.services.document_service import InvalidPDF, prepare_pdf, image_data_url, add_pdf_limitations
from backend.services.storage_service import InvalidImage, StorageService, ScreenshotStorage, validate_image


class ResourceNotFound(LookupError):
    pass


async def _offload(function, on_cancel=None):
    # A cancelled await must not race a still-running filesystem/DB mutation.
    task = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if on_cancel is not None:
            on_cancel()
        await task
        raise


def source_detail(db: Session, source_id: str) -> SourceDetailResponse:
    source = sources.get_source(db, source_id)
    if source is None:
        raise ResourceNotFound("Source not found")
    return SourceDetailResponse(
        source=source,
        snapshots=sources.list_snapshots(db, source_id),
        analyses=analyses.list_source_analyses(db, source_id),
    )


def competitor_detail(db: Session, competitor_id: str) -> CompetitorDetailResponse:
    competitor = competitors.get_competitor(db, competitor_id)
    if competitor is None:
        raise ResourceNotFound("Competitor not found")
    return CompetitorDetailResponse(
        **{field: getattr(competitor, field) for field in (
            "id", "name", "website_url", "niche", "notes", "created_at", "updated_at",
        )},
        sources=sources.list_sources(db, competitor_id),
        latest_analysis=analyses.get_latest_competitor_analysis(db, competitor_id),
    )


def _competitor_name(db: Session, competitor_id: str) -> str:
    try:
        competitor = competitors.get_competitor(db, competitor_id)
        if competitor is None:
            raise ResourceNotFound("Competitor not found")
        return competitor.name
    finally:
        # End the read transaction before awaiting the provider.
        db.rollback()


class IngestionService:
    async def ingest_text(self, db: Session, competitor_id: str, payload: TextSourceCreate):
        return await self._ingest(db, competitor_id, payload.label, text=payload.text)

    async def ingest_image(
        self, db: Session, competitor_id: str, *, label: str,
        content: bytes, filename: str, declared_mime: str | None,
    ):
        return await self._ingest(
            db, competitor_id, label, content=content, filename=filename, declared_mime=declared_mime,
        )

    async def ingest_pdf(self, db: Session, competitor_id: str, *, label: str,
                         content: bytes, filename: str, declared_mime: str | None):
        return await self._ingest(db, competitor_id, label, content=content,
                                  filename=filename, declared_mime=declared_mime, pdf=True)

    async def _ingest(
        self, db: Session, competitor_id: str, label: str, *, text: str | None = None,
        content: bytes | None = None, filename: str | None = None, declared_mime: str | None = None,
        pdf: bool = False,
    ) -> SourceDetailResponse:
        await _offload(lambda: _competitor_name(db, competitor_id))
        source_id, snapshot_id = str(uuid4()), str(uuid4())
        storage = StorageService(settings.upload_dir)
        stored = None
        source_committed = False
        initial_cancelled = Event()
        metadata = {"source_id": source_id, "snapshot_id": snapshot_id}
        source_fields = {
            "id": source_id, "competitor_id": competitor_id, "label": label,
            "source_type": SourceType.pdf if pdf else (SourceType.text if content is None else SourceType.image),
        }
        try:
            if content is not None:
                if pdf:
                    document = await _offload(lambda: prepare_pdf(
                        content, declared_mime, settings.max_pdf_mb * 1024 * 1024,
                        settings.max_pdf_pages_analyzed, settings.max_text_chars))
                    text = document.extracted_text
                    metadata.update(document.metadata)
                    del document  # Rebuilt from persisted bytes; release temporary page images.
                    extension, mime_type = ".pdf", "application/pdf"
                else:
                    image = await _offload(lambda: validate_image(content, declared_mime, settings.max_image_mb * 1024 * 1024))
                    extension, mime_type = image.extension, image.mime_type
                    metadata.update(width=image.width, height=image.height)

                def save_file():
                    nonlocal stored
                    stored = storage.save(content, extension)

                await _offload(save_file)
                source_fields.update(
                    original_filename=filename, mime_type=mime_type,
                    storage_path=stored.storage_path, sha256=stored.sha256,
                )
                metadata.update(
                    original_filename=filename, mime_type=mime_type,
                    storage_path=stored.storage_path, sha256=stored.sha256,
                )

            def persist_source():
                nonlocal source_committed
                try:
                    if competitors.get_competitor(db, competitor_id) is None:
                        raise ResourceNotFound("Competitor not found")
                    sources.create_source(db, **source_fields)
                    sources.create_snapshot(
                        db, id=snapshot_id, source_id=source_id, extracted_text=text, metadata=metadata,
                    )
                    if initial_cancelled.is_set():
                        raise asyncio.CancelledError()
                    db.commit()
                    source_committed = True
                except BaseException:
                    db.rollback()
                    raise

            await _offload(persist_source, on_cancel=initial_cancelled.set)
            return await self.reanalyze(db, source_id)
        except BaseException:
            if stored is not None and not source_committed:
                await _offload(lambda: storage.delete(stored.storage_path))
            raise

    async def ingest_url(self, db: Session, competitor_id: str, payload):
        await _offload(lambda: _competitor_name(db, competitor_id))
        requested = validate_url(str(payload.url)).url
        return await self._capture_url(db, competitor_id, requested, payload.label)

    async def refresh(self, db: Session, source_id: str):
        def load():
            try:
                source = sources.get_source(db, source_id)
                if source is None:
                    raise ResourceNotFound("Source not found")
                if source.source_type != SourceType.url:
                    raise InvalidURL("SOURCE_NOT_REFRESHABLE")
                return source.competitor_id, source.url, source.label
            finally:
                db.rollback()
        competitor_id, requested, label = await _offload(load)
        return await self._capture_url(db, competitor_id, requested, label, source_id=source_id)

    async def _capture_url(self, db, competitor_id, requested, label, *, source_id=None):
        capture = await browser_service.capture(requested)
        storage = ScreenshotStorage(settings.screenshot_dir)
        stored = None
        committed = False
        cancelled = Event()
        new_source = source_id is None
        source_id = source_id or str(uuid4())
        snapshot_id = str(uuid4())
        try:
            def save():
                nonlocal stored
                validate_image(capture.screenshot, "image/png", settings.max_image_mb * 1024 * 1024)
                stored = storage.save(capture.screenshot, ".png")
            await _offload(save)
            def persist():
                nonlocal committed
                try:
                    if new_source:
                        sources.create_source(db, id=source_id, competitor_id=competitor_id,
                                              source_type=SourceType.url, label=label, url=capture.requested_url)
                    elif sources.get_source(db, source_id) is None:
                        raise ResourceNotFound("Source not found")
                    metadata = {**capture.metadata, "source_id": source_id, "snapshot_id": snapshot_id,
                                "screenshot_sha256": stored.sha256}
                    sources.create_snapshot(db, id=snapshot_id, source_id=source_id,
                        final_url=capture.final_url, title=capture.title, meta_description=capture.meta_description,
                        extracted_text=capture.extracted_text, screenshot_path=stored.storage_path, metadata=metadata)
                    if cancelled.is_set():
                        raise asyncio.CancelledError()
                    db.commit()
                    committed = True
                except BaseException:
                    db.rollback()
                    raise
            await _offload(persist, on_cancel=cancelled.set)
            return await self.reanalyze(db, source_id, snapshot_id=snapshot_id)
        except BaseException:
            if stored is not None and not committed:
                await _offload(lambda: storage.delete(stored.storage_path))
            raise

    async def reanalyze(self, db: Session, source_id: str, *, snapshot_id=None) -> SourceDetailResponse:
        prepared, competitor_id, snapshot_id = await _offload(lambda: _load_prepared(db, source_id, snapshot_id))
        model_id = settings.ai_model
        start = perf_counter()
        ai_service.reset_token_usage()
        result = await ai_service.analyze_source(prepared)
        input_tokens, output_tokens = ai_service.token_usage
        if not isinstance(result, CompetitorAnalysis):
            raise AIResponseError("Missing validated CompetitorAnalysis")
        try:
            result = CompetitorAnalysis.model_validate_json(result.model_dump_json(), strict=True)
        except ValidationError as exc:
            raise AIResponseError("Invalid CompetitorAnalysis") from exc
        if prepared.source_type == SourceType.pdf:
            result = add_pdf_limitations(result, prepared.origin_metadata)
        duration_ms = round((perf_counter() - start) * 1000)

        def persist_analysis():
            try:
                # A source may have been explicitly deleted while AI was running.
                source = sources.get_source(db, source_id)
                snapshot = sources.get_snapshot(db, snapshot_id)
                if source is None or snapshot is None or snapshot.source_id != source_id:
                    raise ResourceNotFound("Source or snapshot not found")
                analyses.create_analysis(
                    db, competitor_id=competitor_id, snapshot_id=snapshot_id,
                    analysis_type="source", model_id=model_id,
                    prompt_version=ANALYSIS_PROMPT_VERSION,
                    result=result,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    duration_ms=duration_ms,
                )
                response = source_detail(db, source_id)
                db.commit()
                return response
            except BaseException:
                db.rollback()
                raise

        return await _offload(persist_analysis)


def _load_prepared(db: Session, source_id: str, snapshot_id=None):
    try:
        source = sources.get_source(db, source_id)
        if source is None:
            raise ResourceNotFound("Source not found")
        snapshot = sources.get_snapshot(db, snapshot_id) if snapshot_id else sources.get_latest_snapshot(db, source_id)
        if snapshot is None or snapshot.source_id != source_id:
            raise OSError("Source has no persisted snapshot")
        competitor = competitors.get_competitor(db, source.competitor_id)
        if competitor is None:
            raise ResourceNotFound("Competitor not found")
        competitor_id, snapshot_id = competitor.id, snapshot.id
        fields = dict(
            competitor_name=competitor.name, source_type=source.source_type,
            source_label=source.label,
            text_context=snapshot.extracted_text or "",
            origin_metadata=json.loads(snapshot.metadata_json), image_inputs=[],
        )
        storage_path, mime_type, sha256 = source.storage_path, source.mime_type, source.sha256
        screenshot_path = snapshot.screenshot_path
        if source.source_type == SourceType.url:
            fields["origin_metadata"].update(title=snapshot.title, meta_description=snapshot.meta_description,
                                            captured_at=snapshot.captured_at.isoformat(), final_url=snapshot.final_url)
        fields["origin_metadata"].update(source_id=source_id, snapshot_id=snapshot_id)
    finally:
        db.rollback()
    if fields["source_type"] == SourceType.image:
        if storage_path is None:
            raise OSError("Persisted image has no storage path")
        try:
            content = StorageService(settings.upload_dir).read(storage_path, settings.max_image_mb * 1024 * 1024)
            image = validate_image(content, mime_type, settings.max_image_mb * 1024 * 1024)
        except (ValueError, InvalidImage) as exc:
            raise OSError("Persisted image is not valid") from exc
        if hashlib.sha256(content).hexdigest() != sha256:
            raise OSError("Persisted image hash mismatch")
        fields["image_inputs"] = [image_data_url(content, image.mime_type)]
        fields["text_context"] = "Анализ предоставленного изображения"
    elif fields["source_type"] == SourceType.pdf:
        try:
            content = StorageService(settings.upload_dir).read(storage_path, settings.max_pdf_mb * 1024 * 1024)
            if hashlib.sha256(content).hexdigest() != sha256:
                raise OSError("Persisted PDF hash mismatch")
            metadata = fields["origin_metadata"]
            document = prepare_pdf(content, mime_type, settings.max_pdf_mb * 1024 * 1024,
                                   metadata["analyzed_page_limit"], metadata["text_char_limit"],
                                   selected_pages=metadata["selected_pages"])
            if document.metadata["page_count"] != metadata["page_count"]:
                raise OSError("Persisted PDF page count mismatch")
            fields["image_inputs"] = document.image_inputs
        except (ValueError, TypeError, KeyError, InvalidPDF) as exc:
            raise OSError("Persisted PDF is not valid") from exc
    elif fields["source_type"] == SourceType.url:
        try:
            content = ScreenshotStorage(settings.screenshot_dir).read(screenshot_path, settings.max_image_mb * 1024 * 1024)
            validate_image(content, "image/png", settings.max_image_mb * 1024 * 1024)
            if hashlib.sha256(content).hexdigest() != fields["origin_metadata"]["screenshot_sha256"]:
                raise OSError("Persisted screenshot hash mismatch")
        except (ValueError, TypeError, KeyError) as exc:
            raise OSError("Persisted screenshot is not valid") from exc
        fields["image_inputs"] = [image_data_url(content, "image/png")]
    elif fields["source_type"] != SourceType.text:
        raise OSError("Source type is not supported in this stage")
    elif not fields["text_context"].strip():
        raise OSError("Persisted text content is missing")
    return PreparedAnalysisInput(**fields), competitor_id, snapshot_id


def _delete_with_files(db: Session, records, delete_operation) -> None:
    storage = StorageService(settings.upload_dir)
    removed = []
    try:
        for record in records:
            if record.storage_path:
                backup = storage.remove_reversibly(record.storage_path)
                if backup is not None:
                    removed.append((storage, backup))
            screenshots = ScreenshotStorage(settings.screenshot_dir)
            for snapshot in sources.list_snapshots(db, record.id):
                for path in (snapshot.screenshot_path, snapshot.secondary_screenshot_path):
                    if path:
                        backup = screenshots.remove_reversibly(path)
                        if backup is not None:
                            removed.append((screenshots, backup))
        delete_operation()
        db.commit()
    except BaseException:
        try:
            db.rollback()
        finally:
            for owner, backup in removed:
                owner.restore(backup)
        raise


def delete_source(db: Session, source_id: str) -> None:
    source = sources.get_source(db, source_id)
    if source is None:
        raise ResourceNotFound("Source not found")
    _delete_with_files(db, [source], lambda: sources.delete_source(db, source_id))


def delete_competitor(db: Session, competitor_id: str) -> bool:
    if competitors.get_competitor(db, competitor_id) is None:
        return False
    _delete_with_files(
        db, sources.list_sources(db, competitor_id),
        lambda: competitors.delete_competitor(db, competitor_id),
    )
    return True


ingestion_service = IngestionService()
