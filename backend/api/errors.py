"""One v2 error boundary, shared by every router and framework exception handler."""
import logging
import traceback

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

from backend.models.api import SourceErrorResponse
from backend.security.url_validation import InvalidURL
from backend.services.ai_service import AIConfigurationError, AIProviderError, AIProviderTimeoutError
from backend.services.analysis_service import AnalysisDataNotReady
from backend.services.browser_service import BrowserCaptureError, BrowserTimeoutError
from backend.services.document_service import InvalidPDF, PDFTooLarge
from backend.services.ingestion_service import ResourceNotFound
from backend.services.storage_service import ImageTooLarge, InvalidImage

logger = logging.getLogger('competitor_monitor.api')
ERROR_RESPONSES = {status: {'model': SourceErrorResponse} for status in (400, 404, 413, 422, 500, 502, 504)}


def error_response(request, exc):
    if isinstance(exc, RequestValidationError):
        status, code, message = 422, 'VALIDATION_ERROR', 'Invalid request payload'
    elif isinstance(exc, HTTPException):
        status = exc.status_code
        code, message = {
            400: ('INVALID_REQUEST', 'Invalid request'), 404: ('NOT_FOUND', 'Requested resource not found'),
            405: ('METHOD_NOT_ALLOWED', 'Method not allowed'), 413: ('UPLOAD_TOO_LARGE', 'Upload exceeds size limit'),
            422: ('VALIDATION_ERROR', 'Invalid request payload'),
        }.get(status, ('REQUEST_ERROR', 'Request failed'))
    elif isinstance(exc, AnalysisDataNotReady):
        status, code, message = 400, 'ANALYSIS_DATA_NOT_READY', 'No usable analyses available for this operation'
    elif isinstance(exc, InvalidURL):
        status, code, message = 400, exc.code, 'Этот адрес нельзя анализировать.'
    elif isinstance(exc, BrowserTimeoutError):
        status, code, message = 504, 'BROWSER_TIMEOUT', 'Browser capture timed out'
    elif isinstance(exc, BrowserCaptureError):
        status, code, message = 500, 'BROWSER_ERROR', 'Browser capture failed; upload a screenshot instead'
    elif isinstance(exc, ResourceNotFound):
        status, code, message = 404, 'NOT_FOUND', 'Requested resource not found'
    elif isinstance(exc, PDFTooLarge):
        status, code, message = 413, 'PDF_TOO_LARGE', 'PDF exceeds MAX_PDF_MB'
    elif isinstance(exc, InvalidPDF):
        status, code, message = 400, 'INVALID_PDF', 'Only valid unencrypted PDF files are supported'
    elif isinstance(exc, ImageTooLarge):
        status, code, message = 413, 'IMAGE_TOO_LARGE', 'Image exceeds MAX_IMAGE_MB'
    elif isinstance(exc, InvalidImage):
        status, code, message = 400, 'INVALID_IMAGE', 'Only valid JPEG, PNG and WebP images are supported'
    elif isinstance(exc, AIConfigurationError):
        status, code, message = 500, 'AI_NOT_CONFIGURED', 'Configure AI_API_KEY to analyze sources'
    elif isinstance(exc, AIProviderTimeoutError):
        status, code, message = 504, 'AI_TIMEOUT', 'AI provider timed out'
    elif isinstance(exc, AIProviderError):
        status, code, message = 502, 'AI_PROVIDER_ERROR', 'AI analysis failed'
    else:
        status, code, message = 500, 'INTERNAL_ERROR', 'Operation failed'
        # Keep stack frames and type for diagnosis, excluding exception values,
        # chained provider responses and locals that may contain documents/secrets.
        logger.error('unexpected_failure code=%s exception_type=%s\n%s', code, type(exc).__name__,
                     ''.join(f'  {frame.filename}:{frame.lineno} in {frame.name}\n'
                             for frame in traceback.extract_tb(exc.__traceback__)))
    request.state.error_code = code
    return JSONResponse(status_code=status, content={'error': {'code': code, 'message': message, 'details': None}})


class V2Route(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                return await handler(request)
            except Exception as exc:
                return error_response(request, exc)
        return safe_handler


def register_error_handlers(app):
    async def handle(request, exc):
        return error_response(request, exc)
    app.add_exception_handler(HTTPException, handle)
    app.add_exception_handler(RequestValidationError, handle)
