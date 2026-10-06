"""Competitor Intelligence v2: Web workspace and API only."""
import logging
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api.errors import error_response, register_error_handlers
from backend.api.health import router as health_router
from backend.api.competitors import router as competitors_router
from backend.api.sources import router as sources_router
from backend.api.analyses import router as analyses_router
from backend.api.comparisons import router as comparisons_router
from backend.config import PROJECT_ROOT, settings
from backend.lifespan import lifespan

logger = logging.getLogger('competitor_monitor.api')
app = FastAPI(title='Competitor Intelligence', description='Evidence-based competitor workspace',
              version='2.0.0', lifespan=lifespan, debug=False)
register_error_handlers(app)
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins,
                   allow_credentials=False, allow_methods=['GET', 'POST', 'PATCH', 'DELETE', 'OPTIONS'],
                   allow_headers=['Content-Type'])


@app.middleware('http')
async def log_requests(request: Request, call_next):
    start = perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        # Includes response/file failures outside a route's handler. Reuse the
        # same safe envelope and diagnostic logging, never a raw exception.
        response = error_response(request, exc)
    route = request.scope.get('route')
    # Route templates avoid recording arbitrary path values; no query/body/headers.
    path = getattr(route, 'path', None) or '/unmatched'
    logger.info('request method=%s path=%s status=%s duration_ms=%.1f code=%s',
                request.method, path, response.status_code, (perf_counter()-start)*1000,
                getattr(request.state, 'error_code', 'OK' if response.status_code < 400 else 'HTTP_ERROR'))
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    if request.method in {'GET', 'HEAD'} and (request.url.path == '/' or request.url.path.startswith('/static/')):
        # Revalidate HTML/modules on navigation; do not reuse an old onboarding UI.
        response.headers['Cache-Control'] = 'no-cache, max-age=0, must-revalidate'
    return response


for router in (health_router, competitors_router, sources_router, analyses_router, comparisons_router):
    app.include_router(router)


@app.get('/', include_in_schema=False)
async def root():
    return FileResponse(PROJECT_ROOT / 'frontend/index.html')


app.mount('/static', StaticFiles(directory=PROJECT_ROOT / 'frontend'), name='static')
