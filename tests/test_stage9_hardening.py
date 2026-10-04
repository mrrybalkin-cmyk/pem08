"""Cutover contracts: real routing with deterministic failure injection."""
import ast
import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from backend.config import Settings, settings
from backend.services.ai_service import AIProviderError, AIProviderTimeoutError, ai_service
from backend.services.browser_service import BrowserTimeoutError, browser_service
from backend.repositories import competitors
from test_sources import source_api
from test_url_sources import url_api


def envelope(response, status, code):
    assert response.status_code == status, response.text
    value = response.json()
    assert set(value) == {"error"}
    assert set(value["error"]) == {"code", "message", "details"}
    assert value["error"]["code"] == code
    assert value["error"]["details"] is None
    assert value["error"]["message"]
    assert "PRIVATE_SENTINEL" not in response.text


@pytest.mark.parametrize("method,path,payload", [
    ("get", "/competitors/missing", None), ("patch", "/competitors/missing", {"name": "Valid"}),
    ("delete", "/competitors/missing", None), ("get", "/sources/missing", None),
    ("delete", "/sources/missing", None), ("post", "/sources/missing/reanalyze", None),
    ("post", "/sources/missing/refresh", None), ("get", "/analyses/missing", None),
    ("get", "/competitors/missing/analyses", None),
    ("post", "/competitors/missing/aggregate-analysis", None),
    ("post", "/comparisons", {"competitor_ids": ["missing1", "missing2"]}),
    ("get", "/unknown", None),
])
def test_all_not_found_envelopes(source_api, method, path, payload):
    envelope(source_api.client.request(method, "/api/v2" + path, json=payload), 404, "NOT_FOUND")


@pytest.mark.parametrize("method,path,payload", [
    ("post", "/competitors", {"name": ""}), ("patch", "/competitors/missing", {"name": None}),
    ("post", "/competitors/missing/sources/text", {"text": "x"}),
    ("post", "/competitors/missing/sources/url", {"url": "javascript:alert(1)"}),
    ("post", "/comparisons", {"competitor_ids": ["a"]}),
])
def test_global_validation_envelope(source_api, method, path, payload):
    envelope(source_api.client.request(method, "/api/v2" + path, json=payload), 422, "VALIDATION_ERROR")


def test_upload_and_operation_errors(source_api, monkeypatch):
    api = source_api
    root = f"/api/v2/competitors/{api.competitor_id}"
    envelope(api.client.post(root + "/sources/file", files={"file": ("bad.png", b"not image", "image/png")}), 400, "INVALID_IMAGE")
    monkeypatch.setattr(settings, "max_image_mb", 1)
    envelope(api.client.post(root + "/sources/file", files={"file": ("large.png", b"x" * (1024*1024+1), "image/png")}), 413, "IMAGE_TOO_LARGE")
    envelope(api.client.post(root + "/aggregate-analysis"), 400, "ANALYSIS_DATA_NOT_READY")
    envelope(api.client.post(root + "/sources/url", json={"url": "http://127.0.0.1/private"}), 400, "URL_BLOCKED_PRIVATE_NETWORK")


@pytest.mark.parametrize("exception,status,code", [
    (AIProviderError("PRIVATE_SENTINEL"), 502, "AI_PROVIDER_ERROR"),
    (AIProviderTimeoutError("PRIVATE_SENTINEL"), 504, "AI_TIMEOUT"),
])
def test_provider_error_envelope(source_api, monkeypatch, exception, status, code):
    monkeypatch.setattr(ai_service, "analyze_source", AsyncMock(side_effect=exception))
    envelope(source_api.client.post(f"/api/v2/competitors/{source_api.competitor_id}/sources/text",
                                   json={"text": "PRIVATE_SENTINEL document"}), status, code)


def test_browser_timeout_envelope(url_api, monkeypatch):
    monkeypatch.setattr(browser_service, "capture", AsyncMock(side_effect=BrowserTimeoutError("PRIVATE_SENTINEL")))
    envelope(url_api.client.post(f"/api/v2/competitors/{url_api.competitor_id}/sources/url",
                                json={"url": "https://public.example"}), 504, "BROWSER_TIMEOUT")


@pytest.mark.parametrize("method,function,payload", [
    ("get", "list_competitors", None), ("post", "create_competitor", {"name": "PRIVATE_SENTINEL"}),
    ("patch", "update_competitor", {"name": "PRIVATE_SENTINEL"}),
])
def test_unexpected_failure_logging_is_diagnostic_without_payload(source_api, monkeypatch, caplog, method, function, payload):
    def broken(*args, **kwargs):
        raise RuntimeError("PRIVATE_SENTINEL document key path C:/secret")
    monkeypatch.setattr(competitors, function, broken)
    url = "/api/v2/competitors" + (f"/{source_api.competitor_id}" if method == "patch" else "")
    with caplog.at_level(logging.INFO, logger="competitor_monitor"):
        envelope(source_api.client.request(method, url + "?secret=PRIVATE_SENTINEL", json=payload,
                                           headers={"Authorization": "Bearer PRIVATE_SENTINEL"}), 500, "INTERNAL_ERROR")
    records = [r.getMessage() for r in caplog.records if r.name.startswith("competitor_monitor")]
    assert any("exception_type=RuntimeError" in text and "in broken" in text for text in records)
    assert any("method=" in text and "status=500" in text and "duration_ms=" in text and "code=INTERNAL_ERROR" in text for text in records)
    assert all("PRIVATE_SENTINEL" not in text and "Bearer" not in text for text in records)


def test_cors_actual_and_preflight(source_api):
    client = source_api.client
    allowed, hostile = settings.allowed_origins[0], "https://hostile.example"
    for origin in (allowed, hostile):
        response = client.get("/api/v2/health", headers={"Origin": origin})
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == (origin if origin == allowed else None)
        assert "access-control-allow-credentials" not in response.headers
        preflight = client.options("/api/v2/competitors", headers={"Origin": origin,
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Content-Type"})
        assert preflight.status_code == (200 if origin == allowed else 400)
        assert preflight.headers.get("access-control-allow-origin") == (origin if origin == allowed else None)
        assert "access-control-allow-credentials" not in preflight.headers
    assert client.options("/api/v2/competitors", headers={"Origin": allowed,
        "Access-Control-Request-Method": "PUT"}).status_code == 400
    assert client.options("/api/v2/competitors", headers={"Origin": allowed,
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Authorization"}).status_code == 400


def test_cors_config_is_explicit():
    assert Settings(_env_file=None, cors_origins="https://configured.example,http://localhost:9000").allowed_origins == ["https://configured.example", "http://localhost:9000"]
    assert Settings(_env_file=None, cors_origins="").allowed_origins == []
    for origin in ("*", "https://*.example", "https://user:pass@host", "https://host/path", "null", "javascript:x"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, cors_origins=origin)


def test_openapi_cutover(source_api):
    client = source_api.client
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        "/api/v2/health": {"get"}, "/api/v2/competitors": {"get", "post"},
        "/api/v2/competitors/{competitor_id}": {"get", "patch", "delete"},
        "/api/v2/competitors/{competitor_id}/sources/text": {"post"},
        "/api/v2/competitors/{competitor_id}/sources/file": {"post"},
        "/api/v2/competitors/{competitor_id}/sources/url": {"post"},
        "/api/v2/sources/{source_id}": {"get", "delete"},
        "/api/v2/sources/{source_id}/reanalyze": {"post"}, "/api/v2/sources/{source_id}/refresh": {"post"},
        "/api/v2/sources/{source_id}/snapshots/{snapshot_id}/artifact": {"get"},
        "/api/v2/competitors/{competitor_id}/analyses": {"get"}, "/api/v2/analyses/{analysis_id}": {"get"},
        "/api/v2/competitors/{competitor_id}/aggregate-analysis": {"post"}, "/api/v2/comparisons": {"post"},
    }
    assert set(paths) == set(expected)
    for path, methods in expected.items():
        assert set(paths[path]) == methods
        for method in methods:
            for status in (400, 404, 413, 422, 500, 502, 504):
                schema = paths[path][method]["responses"][str(status)]["content"]["application/json"]["schema"]
                assert schema == {"$ref": "#/components/schemas/SourceErrorResponse"}
    for path in ("/analyze_text", "/analyze_image", "/parse_demo", "/history", "/health"):
        envelope(client.get(path), 404, "NOT_FOUND")
    for path in ("/", "/docs", "/redoc", "/static/js/app.js", "/static/workspace.css"):
        assert client.get(path).status_code == 200


def test_runtime_dependency_and_legacy_static_contract():
    root = Path(__file__).resolve().parents[1]
    forbidden = {"selenium", "webdriver_manager", "PyQt6", "backend.services.openai_service", "backend.services.parser_service", "backend.services.history_service", "backend.models.schemas"}
    for path in (root / "backend").rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(content)):
            imports = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert all(not any(name == term or name.startswith(term + ".") for term in forbidden) for name in imports), path
        assert "history.json" not in content
        assert "@app.on_event" not in content
    requirements = (root / "requirements.txt").read_text(encoding="utf-8").lower()
    assert all(term not in requirements for term in ("selenium", "webdriver-manager", "pyqt6", "beautifulsoup4", "lxml", "aiofiles"))
    for path in (root / "frontend").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        assert all(term not in text for term in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function")), path
    assert not (root / "desktop/main.py").exists()
    assert not (root / "history.json").exists()
    service = (root / "backend/services/ai_service.py").read_text(encoding="utf-8")
    assert "chat.completions.parse(" in service and "import re" not in service


def test_unexpected_non_v2_failure_uses_same_safe_boundary(source_api, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("PRIVATE_SENTINEL root file failure")
    monkeypatch.setattr("backend.main.FileResponse", broken)
    envelope(source_api.client.get("/"), 500, "INTERNAL_ERROR")
