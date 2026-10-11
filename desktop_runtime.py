"""Desktop bootstrap and server ownership; deliberately no backend imports."""

import asyncio
from dataclasses import dataclass
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import sys
from threading import Event, Thread
import traceback
from urllib.parse import urlsplit

from dotenv import dotenv_values


class DesktopError(RuntimeError):
    """Safe error suitable for the native GUI (never an underlying exception)."""


class DiagnosticFormatter(logging.Formatter):
    def format(self, record):
        if not record.exc_info and record.getMessage().startswith("Traceback (most recent call last):"):
            # ASGI lifespan failure messages arrive as preformatted tracebacks.
            record.msg = "lifespan_failure (exception values omitted)"
            record.args = ()
        return super().format(record)

    def formatException(self, exc_info):
        # Uvicorn lifespan errors can contain provider/config values.
        return type(exc_info[1]).__name__ + "\n" + "".join(
            f"  {frame.filename}:{frame.lineno} in {frame.name}\n"
            for frame in traceback.extract_tb(exc_info[2])
        )


@dataclass(frozen=True)
class RuntimePaths:
    root: Path

    @property
    def config(self):
        return self.root / ".env"

    @property
    def database(self):
        return self.root / "data/app.db"

    @property
    def log(self):
        return self.root / "logs/competitionmonitor.log"


def runtime_paths(root=None):
    if root is None:
        local = os.environ.get("LOCALAPPDATA")
        if not local or not Path(local).is_absolute():
            raise DesktopError("LOCALAPPDATA недоступен: невозможно определить каталог данных.")
        root = Path(local) / "CompetitionMonitor"
    root = Path(root)
    if not root.is_absolute():
        raise DesktopError("Каталог desktop данных должен быть абсолютным.")
    return RuntimePaths(root.resolve())


def prepare_runtime(paths):
    # Reject contaminated startup rather than silently reusing Web globals.
    if any(name in sys.modules for name in ("backend.config", "backend.database", "backend.main")):
        raise DesktopError("Backend импортирован до desktop configuration. Перезапустите приложение.")
    if not paths.config.is_file():
        raise DesktopError(f"Создайте configuration file и укажите AI_API_KEY:\n{paths.config}")
    try:
        values = dotenv_values(paths.config, encoding="utf-8", interpolate=False)
    except (OSError, UnicodeError):
        raise DesktopError(f"Не удалось прочитать configuration file:\n{paths.config}") from None
    if not (values.get("AI_API_KEY") or "").strip():
        raise DesktopError(f"Укажите непустой AI_API_KEY в configuration file:\n{paths.config}")

    # Remove inherited application overrides. The selected file is the only
    # source for provider settings; paths/listener are owned by this launcher.
    for key in list(os.environ):
        if key.startswith(("AI_", "APP_", "API_", "MAX_", "BROWSER_")) or key in {
            "DATABASE_URL", "UPLOAD_DIR", "SCREENSHOT_DIR", "CORS_ORIGINS", "LOG_LEVEL",
        }:
            os.environ.pop(key, None)
    os.environ.update(
        PEM08_DESKTOP_CONFIG=str(paths.config), APP_ENV="desktop", APP_HOST="127.0.0.1",
        DATABASE_URL=f"sqlite:///{paths.database.as_posix()}",
        UPLOAD_DIR=str(paths.root / "uploads"), SCREENSHOT_DIR=str(paths.root / "screenshots"),
        CORS_ORIGINS="", LOG_LEVEL="INFO",
    )
    for child in ("data", "uploads", "screenshots", "logs"):
        (paths.root / child).mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(paths.log, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(DiagnosticFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    for name in ("httpx", "httpcore", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("competitionmonitor.desktop").info("configuration_ready")


def held_socket():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Windows must not allow a second listener to share this address.
        if sys.platform == "win32":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(socket.SOMAXCONN)
        listener.setblocking(False)
        return listener
    except BaseException:
        listener.close()
        raise


def load_application():
    from backend.main import app
    return app


def navigation_action(origin, url):
    target = urlsplit(url)
    if target.scheme in {"http", "https"} and not target.username and not target.password:
        if (target.scheme, target.netloc) == (urlsplit(origin).scheme, urlsplit(origin).netloc):
            return "internal"
        return "external"
    return "blocked"


class BackendWorker:
    """One loop/thread owns the ASGI app, browser and lifespan cleanup."""

    def __init__(self, listener, app_loader=load_application):
        self.listener = listener
        self.port = listener.getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.app_loader = app_loader
        self.loop = self.server = None
        self.error = None
        self.done = Event()
        self.stop_requested = Event()
        self.thread = Thread(target=self._run, name="competitionmonitor-backend", daemon=False)

    def start(self):
        os.environ["APP_PORT"] = str(self.port)
        self.thread.start()

    def stop(self):
        self.stop_requested.set()
        if self.loop is not None and not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self._request_exit)
            except RuntimeError:
                pass  # Loop completed concurrently; done/thread monitor owns exit.

    def _request_exit(self):
        if self.server is not None:
            self.server.should_exit = True

    def _run(self):
        logger = logging.getLogger("competitionmonitor.desktop")
        try:
            import uvicorn
            app = self.app_loader()
            factory = asyncio.ProactorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
            with asyncio.Runner(loop_factory=factory) as runner:
                self.loop = runner.get_loop()
                self.server = uvicorn.Server(uvicorn.Config(
                    app, host="127.0.0.1", port=self.port, reload=False, workers=1,
                    log_config=None, access_log=False, proxy_headers=False,
                    timeout_graceful_shutdown=70, lifespan="on",
                ))
                # Queue early-close requests on the owned loop. Pinned Uvicorn
                # executes shutdown whenever startup reached server.started.
                if self.stop_requested.is_set():
                    self.loop.call_soon(self._request_exit)
                logger.info("backend_start port=%s loop=%s", self.port, type(self.loop).__name__)
                runner.run(self.server.serve(sockets=[self.listener]))
                if not self.server.started:
                    self.error = "Backend startup завершился ошибкой. Проверьте desktop log."
                elif self.server.lifespan.shutdown_failed:
                    self.error = "Backend cleanup завершился ошибкой. Проверьте desktop log."
        except BaseException as exc:
            self.error = "Backend остановлен из-за ошибки. Проверьте desktop log."
            # Exception values/locals may include config or provider contents.
            logger.error("backend_failure exception_type=%s", type(exc).__name__)
        finally:
            self.listener.close()
            logger.info("backend_stopped port=%s", self.port)
            self.done.set()


def health_ready(status, payload):
    return status == 200 and isinstance(payload, dict) and payload.get("status") == "ok" and payload.get("database") == "ready"
