"""Explicit local-only Chromium smoke; not collected by offline pytest.

Run with project Python. Test-only validator allows only this ephemeral origin.
Production safety remains unchanged; all other destinations use normal policy.
"""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

from backend.security.url_validation import ValidatedURL, resolve_and_validate_url, BlockedURL
from backend.services.browser_service import BrowserService


hits = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        hits.append(self.path)
        redirects = {"/start": "/second", "/second": "/final", "/blocked": "http://169.254.169.254/private"}
        if self.path in redirects:
            self.send_response(302)
            self.send_header("Location", redirects[self.path])
            self.end_headers()
            return
        body = b'<html><head><title>Local capture</title><meta name="description" content="Local description"></head><body><h1>Visible heading</h1><p>Local visible content</p><img src="http://169.254.169.254/private.png"><iframe src="http://127.0.0.1/private-frame"></iframe><script>new WebSocket(location.origin.replace("http", "ws") + "/ws")</script></body></html>'
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


async def main(origin):
    from backend.config import settings
    settings.max_web_text_chars = 30
    validations = []
    async def local_policy(url):
        validations.append(url)
        parsed = urlsplit(url)
        if url.startswith(origin + "/") and parsed.netloc == urlsplit(origin).netloc:
            return ValidatedURL(url, "http", "127.0.0.1", parsed.port, ("127.0.0.1",))
        return await resolve_and_validate_url(url)
    service = BrowserService(validator=local_policy)
    try:
        result = await service.capture(origin + "/start")
        assert result.final_url == origin + "/final", result.final_url
        assert result.title == "Local capture"
        assert result.meta_description == "Local description"
        assert "Visible heading" in result.extracted_text
        assert len(result.extracted_text) == 30 and result.metadata["text_truncated"]
        assert result.screenshot.startswith(b"\x89PNG\r\n\x1a\n")
        assert not service._contexts
        assert all(origin + path in validations for path in ("/start", "/second", "/final")), validations
        assert any("169.254.169.254/private.png" in url for url in validations), validations
        assert "http://127.0.0.1/private-frame" in validations, validations
        assert "/ws" not in hits, hits
        try:
            await service.capture(origin + "/blocked")
        except BlockedURL:
            pass
        else:
            raise AssertionError("Unsafe redirect accepted")
        assert not service._contexts
        print("LOCAL_CHROMIUM_METADATA_TEXT_PNG_REDIRECT_SUBREQUEST_CLEANUP_OK")
        print("TEST_ONLY_LOOPBACK_INJECTION; PRODUCTION_POLICY_UNCHANGED")
    finally:
        await service.close()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        asyncio.run(main(f"http://127.0.0.1:{server.server_port}"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
