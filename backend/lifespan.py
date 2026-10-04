"""Application lifecycle for Competitor Intelligence Assistant v2."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.config import settings
from backend.database import Base, engine
from backend.models import db as db_models  # noqa: F401
from backend.services.ai_service import ai_service
from backend.services.browser_service import browser_service

logger = logging.getLogger("competitor_monitor")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.started = False
    app.state.database_ready = False
    logger.setLevel(settings.log_level)

    try:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        settings.screenshot_dir.mkdir(parents=True, exist_ok=True)

        await asyncio.to_thread(
            Base.metadata.create_all,
            engine,
        )

        app.state.database_ready = True
        app.state.started = True

        logger.info(
            "Application started; SQLite database ready"
        )

        yield

    finally:
        app.state.started = False
        app.state.database_ready = False

        try:
            await ai_service.close()
        finally:
            try:
                await browser_service.close()
            finally:
                await asyncio.to_thread(engine.dispose)

        logger.info("Application stopped")
