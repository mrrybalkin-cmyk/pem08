"""Persist retryable sources first, then append validated analyses separately."""

import asyncio
import base64
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
from backend.services.storage_service import InvalidImage, StorageService, validate_image


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

    async def _ingest(
        self, db: Session, competitor_id: str, label: str, *, text: str | None = None,
        content: bytes | None = None, filename: str | None = None, declared_mime: str | None = None,
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
            "source_type": SourceType.text if content is None else SourceType.image,
        }
        try:
            if content is not None:
                image = await _offload(lambda: validate_image(content, declared_mime, settings.max_image_mb * 1024 * 1024))

                def save_file():
                    nonlocal stored
                    stored = storage.save(content, image.extension)

                await _offload(save_file)
                source_fields.update(
                    original_filename=filename, mime_type=image.mime_type,
                    storage_path=stored.storage_path, sha256=stored.sha256,
                )
                metadata.update(
                    original_filename=filename, mime_type=image.mime_type,
                    width=image.width, height=image.height,
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

    async def reanalyze(self, db: Session, source_id: str) -> SourceDetailResponse:
        prepared, competitor_id, snapshot_id = await _offload(lambda: _load_prepared(db, source_id))
        model_id = settings.ai_model
        start = perf_counter()
        result = await ai_service.analyze_source(prepared)
        if not isinstance(result, CompetitorAnalysis):
            raise AIResponseError("Missing validated CompetitorAnalysis")
        try:
            result = CompetitorAnalysis.model_validate_json(result.model_dump_json(), strict=True)
        except ValidationError as exc:
            raise AIResponseError("Invalid CompetitorAnalysis") from exc
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
                    prompt_version=ANALYSIS_PROMPT_VERSION, result=result, duration_ms=duration_ms,
                )
                response = source_detail(db, source_id)
                db.commit()
                return response
            except BaseException:
                db.rollback()
                raise

        return await _offload(persist_analysis)


def _load_prepared(db: Session, source_id: str):
    try:
        source = sources.get_source(db, source_id)
        if source is None:
            raise ResourceNotFound("Source not found")
        snapshot = sources.get_latest_snapshot(db, source_id)
        if snapshot is None:
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
        fields["image_inputs"] = [f"data:{image.mime_type};base64,{base64.b64encode(content).decode('ascii')}"]
        fields["text_context"] = "Анализ предоставленного изображения"
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
                    removed.append(backup)
        delete_operation()
        db.commit()
    except BaseException:
        try:
            db.rollback()
        finally:
            for backup in removed:
                storage.restore(backup)
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
