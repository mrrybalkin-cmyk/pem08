"""Lazy shared async Chromium, isolated captures and request-time URL policy.

Policy checks do not pin Chromium DNS: actual egress controls remain necessary
to close DNS TOCTOU. Service workers, WebSockets, WebRTC, downloads and popups
are disabled. Two captures maximum; viewport-only PNG; no persistent profile.
"""

import asyncio
from dataclasses import dataclass
from time import perf_counter
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Error, TimeoutError as PlaywrightTimeout

from backend.config import settings
from backend.security.url_validation import InvalidURL, resolve_and_validate_url


class BrowserCaptureError(RuntimeError):
    pass


class BrowserTimeoutError(BrowserCaptureError):
    pass


class BrowserUnavailableError(BrowserCaptureError):
    pass


@dataclass(frozen=True)
class BrowserCapture:
    requested_url: str
    final_url: str
    title: str | None
    meta_description: str | None
    extracted_text: str
    screenshot: bytes
    metadata: dict


async def _finish(awaitable):
    """Wait for cleanup even when caller cancellation arrives."""
    task = asyncio.create_task(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class BrowserService:
    def __init__(self, *, validator=None, playwright_factory=None):
        self.validator = validator or resolve_and_validate_url
        self.factory = playwright_factory or async_playwright
        self._runtime = self._browser = None
        self._lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(2)
        self._contexts = set()

    @property
    def ready(self):
        return self._browser is not None and self._browser.is_connected()

    async def _start(self):
        async with self._lock:
            if self.ready:
                return self._browser
            if self._runtime is not None:
                await self._runtime.stop()
                self._runtime = self._browser = None
            runtime = None
            try:
                runtime = await self.factory().start()
                browser = await runtime.chromium.launch(
                    headless=settings.browser_headless,
                    args=["--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
                )
            except BaseException as exc:
                if runtime is not None:
                    await _finish(runtime.stop())
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise BrowserUnavailableError("Chromium unavailable") from exc
            self._runtime, self._browser = runtime, browser
            return browser

    async def capture(self, raw_url: str) -> BrowserCapture:
        approved = await self.validator(raw_url)
        async with self._slots:
            browser = await self._start()
            context = page = None
            policy_errors = []
            start = perf_counter()
            try:
                async with asyncio.timeout(settings.browser_timeout_ms / 1000):
                    allocation = asyncio.create_task(browser.new_context(
                        viewport={"width": settings.browser_viewport_width, "height": settings.browser_viewport_height},
                        service_workers="block", accept_downloads=False, permissions=[],
                    ))
                    try:
                        context = await asyncio.shield(allocation)
                    except asyncio.CancelledError:
                        context = await allocation
                        raise
                    self._contexts.add(context)
                    context.set_default_timeout(settings.browser_timeout_ms)

                    async def route_request(route):
                        response = None
                        is_main_document = (
                            page is not None
                            and route.request.is_navigation_request()
                            and route.request.frame == page.main_frame
                        )
                        try:
                            target = await self.validator(route.request.url)
                            # Do not let APIRequestContext follow redirects unchecked.
                            response = await route.fetch(url=target.url, max_redirects=0,
                                                         timeout=settings.browser_timeout_ms)
                            location = response.headers.get("location")
                            if 300 <= response.status < 400 and location:
                                await self.validator(urljoin(target.url, location))
                            await route.fulfill(response=response)
                        except InvalidURL as exc:
                            if is_main_document:
                                policy_errors.append(exc)
                            await route.abort()
                        except PlaywrightTimeout:
                            if is_main_document:
                                policy_errors.append(BrowserTimeoutError("Browser request timed out"))
                            await route.abort()
                        except Error:
                            if is_main_document:
                                policy_errors.append(BrowserCaptureError("Browser request failed"))
                            await route.abort()
                        finally:
                            if response is not None:
                                await response.dispose()

                    await context.route("**/*", route_request)
                    await context.route_web_socket("**/*", lambda ws: ws.close())
                    await context.add_init_script("""
                        Object.defineProperty(globalThis, 'RTCPeerConnection', {value: undefined});
                        Object.defineProperty(globalThis, 'webkitRTCPeerConnection', {value: undefined});
                    """)
                    page = await context.new_page()
                    context.on("page", lambda popup: asyncio.create_task(popup.close()))
                    page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
                    response = await page.goto(approved.url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
                    if policy_errors:
                        raise policy_errors[0]
                    if (response is None or response.status >= 400
                            or response.headers.get("content-type", "").split(";")[0].lower() not in {"text/html", "application/xhtml+xml"}):
                        raise BrowserCaptureError("Navigation did not produce an HTML page")
                    final = await self.validator(page.url)
                    await page.locator("body").wait_for(state="attached")
                    title = (await page.title()).strip() or None
                    description = await page.locator('meta[name="description" i]').first.get_attribute("content") if await page.locator('meta[name="description" i]').count() else None
                    description = description.strip() or None if description is not None else None
                    text_data = await page.evaluate("""limit => {
                        const text = (document.body?.innerText || '').trim();
                        return {text: text.slice(0, limit), truncated: text.length > limit};
                    }""", settings.max_web_text_chars)
                    png = await page.screenshot(type="png", full_page=False)
                    if policy_errors:
                        raise policy_errors[0]
                    return BrowserCapture(approved.url, final.url, title, description, text_data["text"], png, {
                        "requested_url": approved.url, "final_url": final.url,
                        "text_truncated": text_data["truncated"], "text_char_limit": settings.max_web_text_chars,
                        "viewport_width": settings.browser_viewport_width, "viewport_height": settings.browser_viewport_height,
                        "screenshot_format": "png", "status_code": response.status,
                        "capture_duration_ms": round((perf_counter() - start) * 1000),
                    })
            except (PlaywrightTimeout, TimeoutError) as exc:
                raise BrowserTimeoutError("Browser capture timed out") from exc
            except Error as exc:
                if policy_errors:
                    raise policy_errors[0] from exc
                raise BrowserCaptureError("Browser capture failed") from exc
            finally:
                if context is not None:
                    try:
                        try:
                            if page is not None:
                                await _finish(page.close())
                        finally:
                            await _finish(context.close())
                    finally:
                        self._contexts.discard(context)

    async def close(self):
        async with self._lock:
            browser, runtime = self._browser, self._runtime
            self._browser = self._runtime = None
            try:
                for context in tuple(self._contexts):
                    await _finish(context.close())
            finally:
                self._contexts.clear()
                try:
                    if browser is not None:
                        await _finish(browser.close())
                finally:
                    if runtime is not None:
                        await _finish(runtime.stop())
        # Permit a new application lifecycle on a different event loop.
        self._lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(2)


browser_service = BrowserService()
