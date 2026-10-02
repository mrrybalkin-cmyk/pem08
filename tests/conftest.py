"""Isolate test settings and prohibit external network access."""

import os
import shutil
import socket
import sys
from pathlib import Path

import httpx
import pytest
from pydantic_settings import DotEnvSettingsSource


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_RUNTIME_ROOT = PROJECT_ROOT / ".pytest-runtime"


# Settings can be imported while pytest is still collecting test modules.
# Therefore the test environment must be established here, before fixtures run.
for name in list(os.environ):
    if name.startswith(
        (
            "AI_",
            "OPENAI_",
            "PROXY_",
            "APP_",
            "API_",
            "MAX_",
            "BROWSER_",
        )
    ) or name in {
        "UPLOAD_DIR",
        "SCREENSHOT_DIR",
        "DATABASE_URL",
        "CORS_ORIGINS",
        "HISTORY_FILE",
        "LOG_LEVEL",
    }:
        os.environ.pop(name, None)


os.environ["UPLOAD_DIR"] = str(
    TEST_RUNTIME_ROOT / "uploads"
)
os.environ["SCREENSHOT_DIR"] = str(
    TEST_RUNTIME_ROOT / "screenshots"
)
os.environ["HISTORY_FILE"] = str(
    TEST_RUNTIME_ROOT / "history.json"
)
os.environ["DATABASE_URL"] = (
    f"sqlite:///{(TEST_RUNTIME_ROOT / 'app.db').as_posix()}"
)


# Never read the developer's real .env during tests.
DotEnvSettingsSource._read_env_files = lambda self: {}


def deny_network(*args, **kwargs):
    raise AssertionError(
        "Network I/O is forbidden in offline tests"
    )


_socket_connect = socket.socket.connect


def guarded_connect(sock, address):
    # Windows implements asyncio's wakeup socketpair using
    # a loopback connection. Permit only that stdlib call site.
    if sys._getframe(1).f_code is getattr(
        socket.socketpair,
        "__code__",
        None,
    ):
        return _socket_connect(sock, address)

    return deny_network()


@pytest.fixture(scope="session", autouse=True)
def offline_environment():
    if TEST_RUNTIME_ROOT.exists():
        shutil.rmtree(TEST_RUNTIME_ROOT)

    TEST_RUNTIME_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            socket.socket,
            "connect",
            guarded_connect,
        )
        patch.setattr(
            socket.socket,
            "connect_ex",
            deny_network,
        )
        patch.setattr(
            socket,
            "getaddrinfo",
            deny_network,
        )
        patch.setattr(
            httpx.HTTPTransport,
            "handle_request",
            deny_network,
        )
        patch.setattr(
            httpx.AsyncHTTPTransport,
            "handle_async_request",
            deny_network,
        )

        try:
            yield
        finally:
            shutil.rmtree(
                TEST_RUNTIME_ROOT,
                ignore_errors=True,
            )


@pytest.fixture
def app():
    from backend.main import app

    return app


@pytest.fixture
def analysis_payload():
    """A complete v2 provider payload; empty arrays/nulls remain explicit."""
    return {
        "executive_summary": "Контекст описывает предложение автоматизации для команд.",
        "positioning": "Автоматизация для небольших команд",
        "target_audience": ["Команды"],
        "value_propositions": ["Автоматизация"],
        "differentiators": [],
        "strengths": ["Ясное предложение"],
        "gaps": ["Нет подтверждения результатов"],
        "marketing_messages": ["Экономия времени"],
        "scorecard": {
            **{name: {"score": 5, "rationale": "Оценка основана на предоставленном тексте"}
               for name in ("positioning_clarity", "value_proposition", "trust", "cta_strength")},
            "visual_consistency": None,
            "ux_clarity": None,
        },
        "evidence": [{
            "category": "positioning", "finding": "Заявлена автоматизация",
            "evidence": "Автоматизация для команд", "source_hint": "Описание",
            "confidence": "high",
        }],
        "opportunities": ["Добавить подтверждение результатов"],
        "recommended_actions": ["Опубликовать кейс"],
        "limitations": ["Доступен только предоставленный текст"],
    }
