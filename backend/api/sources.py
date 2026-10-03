"""Stage 4 source routes; HTTP concerns only, no provider/filesystem workflow."""

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models.api import SourceDetailResponse, SourceErrorResponse, TextSourceCreate
from backend.services.ai_service import AIConfigurationError, AIProviderError, AIProviderTimeoutError
from backend.services.ingestion_service import ResourceNotFound, delete_source, ingestion_service, source_detail
from backend.services.storage_service import ImageTooLarge, InvalidImage


class SourceRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                return await handler(request)
            except ResourceNotFound:
                status, code, message = 404, "NOT_FOUND", "Requested resource not found"
            except ImageTooLarge:
                status, code, message = 413, "IMAGE_TOO_LARGE", "Image exceeds MAX_IMAGE_MB"
            except InvalidImage:
                status, code, message = 400, "INVALID_IMAGE", "Only valid JPEG, PNG and WebP images are supported"
            except RequestValidationError:
                status, code, message = 422, "VALIDATION_ERROR", "Invalid source payload"
            except AIConfigurationError:
                status, code, message = 500, "AI_NOT_CONFIGURED", "Configure AI_API_KEY to analyze sources"
            except AIProviderTimeoutError:
                status, code, message = 504, "AI_TIMEOUT", "AI provider timed out"
            except AIProviderError:
                status, code, message = 502, "AI_PROVIDER_ERROR", "AI analysis failed"
            except Exception:
                status, code, message = 500, "INTERNAL_ERROR", "Source operation failed"
            return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": None}})

        return safe_handler


router = APIRouter(
    prefix="/api/v2", tags=["sources"], route_class=SourceRoute,
    responses={status: {"model": SourceErrorResponse} for status in (400, 404, 413, 422, 500, 502, 504)},
)


@router.post("/competitors/{competitor_id}/sources/text", response_model=SourceDetailResponse, status_code=201)
async def create_text_source(competitor_id: str, payload: TextSourceCreate, db: Session = Depends(get_db)):
    return await ingestion_service.ingest_text(db, competitor_id, payload)


@router.post("/competitors/{competitor_id}/sources/file", response_model=SourceDetailResponse, status_code=201)
async def create_image_source(
    competitor_id: str, file: UploadFile = File(...),
    label: str = Form(default="Изображение", max_length=120), db: Session = Depends(get_db),
):
    try:
        content = await file.read(settings.max_image_mb * 1024 * 1024 + 1)
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
