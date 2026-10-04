"""Run the documented python run.py command against an isolated runtime."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import httpx
from tests.browser_ui_smoke import ROOT, filesystem


def main():
    before = filesystem()
    parent = ROOT / ".pytest-runtime"
    parent.mkdir(exist_ok=True)
    try:
        with TemporaryDirectory(prefix="stage9-startup-", dir=parent) as directory:
            runtime = Path(directory)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            env = os.environ.copy()
            env.update(APP_ENV="test", APP_HOST="127.0.0.1", APP_PORT=str(port), AI_API_KEY="",
                DATABASE_URL=f"sqlite:///{(runtime / 'app.db').as_posix()}",
                UPLOAD_DIR=str(runtime / "uploads"), SCREENSHOT_DIR=str(runtime / "screenshots"),
                AI_BASE_URL="http://127.0.0.1:1/disabled", AI_MODEL="offline", AI_PROVIDER="offline",
                LOG_LEVEL="INFO", CORS_ORIGINS=f"http://127.0.0.1:{port}", PYTHONUTF8="1")
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            with (runtime / "server.log").open("w", encoding="utf-8") as output:
                process = subprocess.Popen([sys.executable, "run.py"], cwd=ROOT, env=env,
                                           stdout=output, stderr=subprocess.STDOUT, creationflags=flags)
                origin = f"http://127.0.0.1:{port}"
                try:
                    deadline = monotonic() + 20
                    with httpx.Client(base_url=origin, trust_env=False, timeout=1) as client:
                        while monotonic() < deadline:
                            assert process.poll() is None, "run.py exited before readiness"
                            try:
                                response = client.get("/api/v2/health")
                                if response.status_code == 200:
                                    break
                            except httpx.TransportError:
                                pass
                            sleep(.1)
                        else:
                            raise AssertionError("run.py did not become ready")
                        assert response.json() == {"status": "ok", "version": "2.0.0", "database": "ready", "browser": "not_initialized", "ai_configured": False}
                        allowed = client.get("/api/v2/health", headers={"Origin": origin})
                        assert allowed.headers["access-control-allow-origin"] == origin
                        hostile = client.get("/api/v2/health", headers={"Origin": "https://hostile.example"})
                        assert "access-control-allow-origin" not in hostile.headers
                        assert "access-control-allow-credentials" not in allowed.headers
                        for path in ("/", "/docs", "/redoc", "/openapi.json", "/static/workspace.css"):
                            assert client.get(path).status_code == 200, path
                        for module in (ROOT / "frontend/js").glob("*.js"):
                            assert client.get("/static/js/" + module.name).status_code == 200, module.name
                        assert client.get("/analyze_text").status_code == 404
                        assert client.get("/api/v2/competitors").json() == []
                        print(f"python run.py: {origin}; root/health/OpenAPI/docs/all static modules=200; keyless honest health PASS")
                finally:
                    if process.poll() is None:
                        process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                            raise AssertionError("run.py did not stop gracefully")
            log = (runtime / "server.log").read_text(encoding="utf-8")
            assert "Application stopped" in log and "Application shutdown complete" in log, log
            assert "Traceback" not in log, log
            assert (runtime / "app.db").is_file()
            print("SINGLE_COMMAND_STARTUP_PASS; graceful shutdown confirmed")
    finally:
        assert filesystem() == before, "Production filesystem changed"
        if not any(parent.iterdir()):
            parent.rmdir()
    print("TEMPORARY_RUNTIME_CLEANED; PRODUCTION_FILESYSTEM_UNCHANGED")


if __name__ == "__main__":
    main()
