"""Isolate settings and prohibit network before importing application modules."""
import os
import socket
import sys

import httpx
import pytest
from pydantic_settings import DotEnvSettingsSource


def deny_network(*args, **kwargs):
    raise AssertionError("Network I/O is forbidden in offline tests")


_socket_connect = socket.socket.connect


def guarded_connect(sock, address):
    # Windows implements asyncio's wakeup socketpair using a loopback connection.
    # Permit only that stdlib call site, not arbitrary loopback requests.
    if sys._getframe(1).f_code is getattr(socket.socketpair, "__code__", None):
        return _socket_connect(sock, address)
    return deny_network()


@pytest.fixture(scope="session", autouse=True)
def offline_environment(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        for name in list(os.environ):
            if name.startswith(("AI_", "OPENAI_", "PROXY_", "APP_", "API_", "MAX_", "BROWSER_")) or name in {
                "UPLOAD_DIR", "SCREENSHOT_DIR", "DATABASE_URL", "CORS_ORIGINS",
                "HISTORY_FILE", "LOG_LEVEL",
            }:
                patch.delenv(name)
        directory = tmp_path_factory.mktemp("stage1")
        patch.setenv("UPLOAD_DIR", str(directory / "uploads"))
        patch.setenv("SCREENSHOT_DIR", str(directory / "screenshots"))
        patch.setenv("HISTORY_FILE", str(directory / "history.json"))
        patch.setattr(DotEnvSettingsSource, "_read_env_files", lambda self: {})
        patch.setattr(socket.socket, "connect", guarded_connect)
        patch.setattr(socket.socket, "connect_ex", deny_network)
        patch.setattr(socket, "getaddrinfo", deny_network)
        patch.setattr(httpx.HTTPTransport, "handle_request", deny_network)
        patch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_network)
        yield


@pytest.fixture
def app():
    from backend.main import app
    return app
