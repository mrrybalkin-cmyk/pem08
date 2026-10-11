"""Offline desktop contracts, runnable in the unchanged Web environment."""

import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from desktop_runtime import DesktopError, health_ready, held_socket, navigation_action, runtime_paths

ROOT = Path(__file__).resolve().parents[1]


def isolated(code, tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONUTF8"] = "1"
    # Child has explicit config, never the developer .env.
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_localappdata_paths_independent_of_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    paths = runtime_paths()
    assert paths.database == tmp_path / "CompetitionMonitor/data/app.db"
    assert paths.config == tmp_path / "CompetitionMonitor/.env"
    assert paths.log == tmp_path / "CompetitionMonitor/logs/competitionmonitor.log"


def test_no_relative_or_missing_runtime_root(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(DesktopError):
        runtime_paths()
    with pytest.raises(DesktopError):
        runtime_paths("relative")


@pytest.mark.parametrize("contents", [None, "AI_API_KEY=\n"])
def test_missing_config_never_imports_backend(tmp_path, contents):
    if contents is not None:
        (tmp_path / ".env").write_text(contents)
    output = isolated("""
import sys
from pathlib import Path
from desktop_runtime import runtime_paths, prepare_runtime, DesktopError
paths = runtime_paths(Path.cwd())
try:
    prepare_runtime(paths)
except DesktopError as exc:
    assert str(paths.config) in str(exc)
else:
    raise AssertionError('Missing key accepted')
assert 'backend.config' not in sys.modules
assert 'backend.main' not in sys.modules
assert not paths.database.exists()
print('CONFIG_FAILURE_SAFE')
""", tmp_path)
    assert "CONFIG_FAILURE_SAFE" in output


def test_configuration_precedes_all_backend_imports(tmp_path):
    (tmp_path / ".env").write_text("AI_API_KEY=offline-test-marker\nAPP_PORT=1234\nDATABASE_URL=sqlite:///unsafe.db\n")
    isolated("""
import os, sys
from pathlib import Path
from desktop_runtime import runtime_paths, prepare_runtime, held_socket, load_application
paths = runtime_paths(Path.cwd())
assert not any(n in sys.modules for n in ('backend.config','backend.database','backend.main'))
prepare_runtime(paths)
listener = held_socket()
os.environ['APP_PORT'] = str(listener.getsockname()[1])
load_application()
from backend.config import settings
from backend.database import engine
assert settings.database_url == 'sqlite:///' + paths.database.as_posix()
assert settings.upload_dir == paths.root / 'uploads'
assert settings.screenshot_dir == paths.root / 'screenshots'
assert settings.app_port == listener.getsockname()[1]
assert settings.model_config['env_file'] == str(paths.config)
assert settings.ai_api_key.get_secret_value() == 'offline-test-marker'
assert not paths.database.exists()  # No startup in this import-only test.
engine.dispose()
listener.close()
""", tmp_path)


def test_reject_preimported_backend(tmp_path):
    isolated("""
import sys
from pathlib import Path
from desktop_runtime import runtime_paths, prepare_runtime, DesktopError
sys.modules['backend.database'] = object()
try:
    prepare_runtime(runtime_paths(Path.cwd()))
except DesktopError:
    pass
else:
    raise AssertionError('Already imported backend accepted')
""", tmp_path)


def test_held_socket_exclusive_and_released():
    listener = held_socket()
    address = listener.getsockname()
    assert address[0] == "127.0.0.1" and address[1] > 0
    with socket.socket() as other:
        with pytest.raises(OSError):
            other.bind(address)
    listener.close()
    with socket.socket() as other:
        other.bind(address)


@pytest.mark.parametrize("status,payload,expected", [
    (200, {"status": "ok", "database": "ready"}, True),
    (503, {"status": "ok", "database": "ready"}, False),
    (200, {"status": "ok", "database": "not_initialized"}, False),
    (200, {"status": "error", "database": "ready"}, False),
    (200, None, False),
])
def test_readiness_contract(status, payload, expected):
    assert health_ready(status, payload) is expected


@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:1234/static/workspace.css", "internal"),
    ("http://127.0.0.1:1235/", "external"),
    ("https://example.com/", "external"),
    ("file:///C:/secret.txt", "blocked"),
    ("javascript:alert(1)", "blocked"),
    ("http://user:password@example.com/", "blocked"),
])
def test_navigation_origin_policy(url, expected):
    assert navigation_action("http://127.0.0.1:1234", url) == expected


def test_worker_failure_closes_listener(monkeypatch):
    from desktop_runtime import BackendWorker
    monkeypatch.setenv("APP_PORT", "8000")
    def broken():
        raise RuntimeError("sensitive error must not escape")
    worker = BackendWorker(held_socket(), broken)
    worker.start()
    worker.thread.join(timeout=10)
    assert worker.done.is_set() and not worker.thread.is_alive()
    assert worker.error and "sensitive" not in worker.error
    assert worker.listener.fileno() == -1
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", worker.port))


def test_real_uvicorn_lifespan_clean_shutdown(monkeypatch):
    from contextlib import asynccontextmanager
    from threading import Event
    from fastapi import FastAPI
    from desktop_runtime import BackendWorker
    monkeypatch.setenv("APP_PORT", "8000")
    entered, cleaned = Event(), Event()
    @asynccontextmanager
    async def lifespan(app):
        entered.set()
        try:
            yield
        finally:
            cleaned.set()
    worker = BackendWorker(held_socket(), lambda: FastAPI(lifespan=lifespan))
    worker.start()
    try:
        assert entered.wait(10)
    finally:
        worker.stop()
        worker.thread.join(timeout=10)
    assert cleaned.is_set() and worker.done.is_set()
    assert not worker.thread.is_alive() and worker.error is None
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", worker.port))


def test_log_traceback_does_not_include_exception_values():
    import logging
    from desktop_runtime import DiagnosticFormatter
    try:
        raise ValueError("secret-config-value")
    except ValueError:
        record = logging.LogRecord("test", logging.ERROR, __file__, 1, "startup_failure", (), sys.exc_info())
    rendered = DiagnosticFormatter().format(record)
    assert "secret-config-value" not in rendered
    assert "ValueError" in rendered and "startup_failure" in rendered


def test_preformatted_lifespan_traceback_is_safe():
    import logging
    from desktop_runtime import DiagnosticFormatter
    record = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1,
                               "Traceback (most recent call last):\nValueError: secret-provider-body", (), None)
    assert DiagnosticFormatter().format(record) == "lifespan_failure (exception values omitted)"
