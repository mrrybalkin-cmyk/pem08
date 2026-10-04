"""Stage 4 source routes; HTTP concerns only, no provider/filesystem workflow."""

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.api.errors import V2Route, ERROR_RESPONSES
from backend.config import settings
from backend.database import get_db
from backend.models.api import SourceDetailResponse, TextSourceCreate, UrlSourceCreate
from backend.services.ingestion_service import ResourceNotFound, delete_source, ingestion_service, source_detail
from backend.services.storage_service import InvalidImage, StorageService, ScreenshotStorage
from backend.repositories import sources as source_repository


router = APIRouter(
    prefix="/api/v2", tags=["sources"], route_class=V2Route,
    responses=ERROR_RESPONSES,
)


@router.post("/competitors/{competitor_id}/sources/text", response_model=SourceDetailResponse, status_code=201)
async def create_text_source(competitor_id: str, payload: TextSourceCreate, db: Session = Depends(get_db)):
    return await ingestion_service.ingest_text(db, competitor_id, payload)


@router.post("/competitors/{competitor_id}/sources/file", response_model=SourceDetailResponse, status_code=201, summary="Upload JPEG/PNG/WebP image or PDF and analyze")
async def create_file_source(
    competitor_id: str, file: UploadFile = File(...),
    label: str = Form(default="Файл", max_length=120), db: Session = Depends(get_db),
):
    try:
        cap = max(settings.max_image_mb, settings.max_pdf_mb) * 1024 * 1024
        content = await file.read(cap + 1)
        # Check spoofing before type-specific size validation; never read beyond cap.
        if content.startswith(b"%PDF") and file.content_type != "application/pdf":
            raise InvalidImage("PDF declared as image")
        if file.content_type == "application/pdf":
            return await ingestion_service.ingest_pdf(
                db, competitor_id, label=label, content=content,
                filename=file.filename or "", declared_mime=file.content_type,
            )
        return await ingestion_service.ingest_image(
            db, competitor_id, label=label, content=content,
            filename=file.filename or "", declared_mime=file.content_type,
        )
    finally:
        await file.close()


@router.get("/sources/{source_id}", response_model=SourceDetailResponse)
def get_source(source_id: str, db: Session = Depends(get_db)):
    return source_detail(db, source_id)


@router.post("/sources/{source_id}/reanalyze", response_model=SourceDetailResponse, status_code=201)
async def reanalyze_source(source_id: str, db: Session = Depends(get_db)):
    return await ingestion_service.reanalyze(db, source_id)


@router.delete("/sources/{source_id}", status_code=204)
def remove_source(source_id: str, db: Session = Depends(get_db)):
    delete_source(db, source_id)
    return Response(status_code=204)


@router.post("/competitors/{competitor_id}/sources/url", response_model=SourceDetailResponse, status_code=201)
async def create_url_source(competitor_id: str, payload: UrlSourceCreate, db: Session = Depends(get_db)):
    return await ingestion_service.ingest_url(db, competitor_id, payload)


@router.post("/sources/{source_id}/refresh", response_model=SourceDetailResponse, status_code=201)
async def refresh_source(source_id: str, db: Session = Depends(get_db)):
    return await ingestion_service.refresh(db, source_id)


@router.get("/sources/{source_id}/snapshots/{snapshot_id}/artifact", response_class=FileResponse)
def preview_artifact(source_id: str, snapshot_id: str, db: Session = Depends(get_db)):
    """Stage 8 preview: DB-owned image/screenshot only, never a client path."""
    source = source_repository.get_source(db, source_id)
    snapshot = source_repository.get_snapshot(db, snapshot_id)
    if source is None or snapshot is None or snapshot.source_id != source_id:
        raise ResourceNotFound("Artifact not found")
    if source.source_type == "image":
        stored_path, storage = source.storage_path, StorageService(settings.upload_dir)
        media_type = source.mime_type
        if media_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ResourceNotFound("Artifact not found")
    elif source.source_type == "url":
        stored_path, storage = snapshot.screenshot_path, ScreenshotStorage(settings.screenshot_dir)
        media_type = "image/png"
    else:
        raise ResourceNotFound("Artifact not found")
    try:
        path = storage.resolve(stored_path) if stored_path else None
    except (ValueError, TypeError):
        raise ResourceNotFound("Artifact not found") from None
    if path is None or not path.is_file():
        raise ResourceNotFound("Artifact not found")
    if source.source_type == "image" and path.suffix != {
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    }[media_type]:
        raise ResourceNotFound("Artifact not found")
    return FileResponse(path, media_type=media_type, headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
        "Content-Disposition": "inline",
    })
