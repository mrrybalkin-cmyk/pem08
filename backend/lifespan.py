"""Offline Stage 1 lifecycle; database and browser are not initialized yet."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.config import settings
from backend.services.openai_service import openai_service
from backend.services.parser_service import parser_service

logger = logging.getLogger("competitor_monitor")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.started = False
    logger.setLevel(settings.log_level)
    try:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        settings.screenshot_dir.mkdir(parents=True, exist_ok=True)
        app.state.started = True
        logger.info("Application started; Stage 1 foundation")
        yield
    finally:
        app.state.started = False
        try:
            await parser_service.close()
        finally:
            await asyncio.to_thread(openai_service.close)
        logger.info("Application stopped")
