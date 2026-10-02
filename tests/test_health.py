import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient


def test_fresh_import_without_credentials(tmp_path):
    # A fresh process catches import-time regressions even after other tests import app.
    script = '''
import socket
import sys
from unittest.mock import patch
from pydantic_settings import DotEnvSettingsSource

def deny(*args, **kwargs):
    raise AssertionError("Unexpected network or client construction")

original_connect = socket.socket.connect
def guarded_connect(sock, address):
    if sys._getframe(1).f_code is getattr(socket.socketpair, "__code__", None):
        return original_connect(sock, address)
    return deny()

socket.socket.connect = guarded_connect
socket.socket.connect_ex = deny
socket.getaddrinfo = deny
with patch.object(DotEnvSettingsSource, "_read_env_files", return_value={}), \
     patch("openai.OpenAI", side_effect=deny), \
     patch("openai.AsyncOpenAI", side_effect=deny):
    import backend.main
    from backend.config import settings
    from fastapi.testclient import TestClient
    assert not settings.ai_configured
    assert not settings.proxy_api_key.get_secret_value()
    print("IMPORT_OK")
    with TestClient(backend.main.app) as client:
        assert backend.main.app.state.started
        response = client.get("/api/v2/health")
        assert response.status_code == 200
        assert response.json()["ai_configured"] is False
    assert not backend.main.app.state.started
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "IMPORT_OK" in result.stdout


def test_health_and_legacy_routes_offline(app):
    with patch("backend.services.openai_service.OpenAI", side_effect=AssertionError("Eager client")):
        with TestClient(app) as client:
            response = client.get("/api/v2/health")
            assert response.status_code == 200
            assert response.json() == {
                "status": "ok", "version": "2.0.0",
                "database": "ready", "browser": "not_initialized",
                "ai_configured": False,
            }
            for path in ("/", "/static/app.js", "/health", "/openapi.json", "/history"):
                assert client.get(path).status_code == 200
            response = client.post("/analyze_text", json={"text": "Offline example text"})
            assert response.json()["success"] is False
            assert "PROXY_API_KEY" in response.json()["error"]
            paths = client.get("/openapi.json").json()["paths"]
            assert {"/analyze_text", "/analyze_image", "/parse_demo", "/history", "/health"} <= paths.keys()


def test_cors_allow_and_deny(app):
    with TestClient(app) as client:
        for origin, expected in [("http://localhost:8000", 200), ("https://untrusted.example", 400)]:
            response = client.options("/api/v2/health", headers={
                "Origin": origin, "Access-Control-Request-Method": "GET",
            })
            assert response.status_code == expected
            assert response.headers.get("access-control-allow-origin") == (origin if expected == 200 else None)


def test_configured_health_still_does_not_create_client(app, monkeypatch):
    from pydantic import SecretStr
    from backend.config import settings
    monkeypatch.setattr(settings, "ai_api_key", SecretStr("test-only-v2-marker"))
    monkeypatch.setattr(settings, "proxy_api_key", SecretStr("test-only-v1-marker"))
    with patch("backend.services.openai_service.OpenAI", side_effect=AssertionError("Eager client")):
        with TestClient(app) as client:
            response = client.get("/api/v2/health")
            assert response.status_code == 200
            assert response.json()["ai_configured"] is True
