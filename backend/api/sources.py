"""Stage 4 source routes; HTTP concerns only, no provider/filesystem workflow."""

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models.api import SourceDetailResponse, SourceErrorResponse, TextSourceCreate, UrlSourceCreate
from backend.services.ai_service import AIConfigurationError, AIProviderError, AIProviderTimeoutError
from backend.services.ingestion_service import ResourceNotFound, delete_source, ingestion_service, source_detail
from backend.services.storage_service import ImageTooLarge, InvalidImage
from backend.services.document_service import InvalidPDF, PDFTooLarge
from backend.security.url_validation import InvalidURL
from backend.services.browser_service import BrowserCaptureError, BrowserTimeoutError


class SourceRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                return await handler(request)
            except InvalidURL as exc:
                status, code, message = 400, exc.code, str(exc)
            except BrowserTimeoutError:
                status, code, message = 504, "BROWSER_TIMEOUT", "Browser capture timed out"
            except BrowserCaptureError:
                status, code, message = 500, "BROWSER_ERROR", "Browser capture failed; upload a screenshot instead"
            except ResourceNotFound:
                status, code, message = 404, "NOT_FOUND", "Requested resource not found"
            except PDFTooLarge:
                status, code, message = 413, "PDF_TOO_LARGE", "PDF exceeds MAX_PDF_MB"
            except InvalidPDF:
                status, code, message = 400, "INVALID_PDF", "Only valid unencrypted PDF files are supported"
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
