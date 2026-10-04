"""Single-command startup: python run.py."""

import sys

import uvicorn

from backend.config import settings


if __name__ == "__main__":
    # Playwright launches its driver as a subprocess. On Windows the
    # Uvicorn development reloader can create an incompatible subprocess
    # environment, so reload is disabled there.
    reload_enabled = (
        settings.app_env == "development"
        and sys.platform != "win32"
    )

    print(
        f"Competitor Intelligence: "
        f"http://{settings.app_host}:{settings.app_port}"
    )

    uvicorn.run(
        "backend.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=reload_enabled,
        log_level=settings.log_level.lower(),
        access_log=False,
        proxy_headers=False,
    )
