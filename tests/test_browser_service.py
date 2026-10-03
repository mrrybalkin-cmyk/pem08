import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from playwright.async_api import Error, TimeoutError as PlaywrightTimeout

from backend.config import settings
from backend.security.url_validation import resolve_and_validate_url, BlockedURL
from backend.services.browser_service import BrowserService, BrowserTimeoutError, BrowserUnavailableError, BrowserCaptureError
from test_sources import image_bytes


@pytest.fixture
def browser_fake():
    locator = SimpleNamespace(wait_for=AsyncMock(), count=AsyncMock(return_value=1),
                              get_attribute=AsyncMock(return_value=" Description "))
    locator.first = locator
    response = SimpleNamespace(headers={"content-type": "text/html"}, status=200)
    pages, contexts = [], []
    async def new_context(**kwargs):
        page = SimpleNamespace(url="https://public.example/", title=AsyncMock(return_value=" Title "),
            evaluate=AsyncMock(return_value={"text": "Visible content", "truncated": True}),
            screenshot=AsyncMock(return_value=image_bytes()), goto=AsyncMock(return_value=response),
            locator=Mock(return_value=locator), on=Mock(), close=AsyncMock(), main_frame=object())
        context = Mock(route=AsyncMock(), route_web_socket=AsyncMock(), add_init_script=AsyncMock(),
                       new_page=AsyncMock(return_value=page), close=AsyncMock())
        pages.append(page)
        contexts.append(context)
        return context
    browser = Mock(new_context=AsyncMock(side_effect=new_context), close=AsyncMock(), is_connected=Mock(return_value=True))
    runtime = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)), stop=AsyncMock())
    factory = Mock(return_value=SimpleNamespace(start=AsyncMock(return_value=runtime)))
    resolver = AsyncMock(return_value=["93.184.216.34"])
    async def validator(url):
        return await resolve_and_validate_url(url, resolver=resolver)
    service = BrowserService(validator=validator, playwright_factory=factory)
    return SimpleNamespace(service=service, browser=browser, runtime=runtime, factory=factory,
                           contexts=contexts, pages=pages, resolver=resolver)


@pytest.mark.asyncio
async def test_lazy_shared_browser_isolated_contexts_and_cleanup(browser_fake):
    f = browser_fake
    assert not f.service.ready
    f.factory.assert_not_called()
    results = await asyncio.gather(f.service.capture("https://public.example/"), f.service.capture("https://public.example/"))
    f.runtime.chromium.launch.assert_awaited_once()
    assert len(f.contexts) == 2
    for context in f.contexts:
        context.close.assert_awaited_once()
        context.route.assert_awaited_once()
        context.route_web_socket.assert_awaited_once()
    kwargs = f.browser.new_context.await_args.kwargs
    assert kwargs["service_workers"] == "block" and kwargs["accept_downloads"] is False
    assert kwargs["permissions"] == []
    assert kwargs["viewport"] == {"width": settings.browser_viewport_width, "height": settings.browser_viewport_height}
    assert results[0].title == "Title" and results[0].meta_description == "Description"
    assert results[0].metadata["text_truncated"]
    f.pages[0].screenshot.assert_awaited_once_with(type="png", full_page=False)
    await f.service.close()
    await f.service.close()
    f.browser.close.assert_awaited_once()
    f.runtime.stop.assert_awaited_once()
    assert not f.service.ready


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [Error("navigation failed"), PlaywrightTimeout("timeout"), asyncio.CancelledError()])
async def test_capture_cleanup_on_error_cancel_and_reuse(browser_fake, error):
    f = browser_fake
    original = f.browser.new_context.side_effect
    async def failed_context(**kwargs):
        context = await original(**kwargs)
        f.pages[-1].goto.side_effect = error
        return context
    f.browser.new_context.side_effect = failed_context
    with pytest.raises(asyncio.CancelledError if isinstance(error, asyncio.CancelledError) else (BrowserTimeoutError if isinstance(error, PlaywrightTimeout) else RuntimeError)):
        await f.service.capture("https://public.example/")
    f.contexts[-1].close.assert_awaited_once()
    f.browser.close.assert_not_awaited()
    f.browser.new_context.side_effect = original
    await f.service.capture("https://public.example/")
    assert len(f.contexts) == 2
    await f.service.close()


def fake_route(url, *, location=None, navigation=False, frame=None):
    response = SimpleNamespace(status=302 if location else 200,
                               headers={"location": location} if location else {}, dispose=AsyncMock())
    return SimpleNamespace(request=SimpleNamespace(url=url, is_navigation_request=lambda: navigation, frame=frame),
        fetch=AsyncMock(return_value=response), fulfill=AsyncMock(), abort=AsyncMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected", [
    ("timeout", BrowserTimeoutError),
    ("transport", BrowserCaptureError),
    ("redirect", BlockedURL),
])
async def test_main_document_request_failure_is_fatal(browser_fake, case, expected):
    f = browser_fake
    original = f.browser.new_context.side_effect
    route = None

    async def context_with_request(**kwargs):
        nonlocal route
        context = await original(**kwargs)
        page = f.pages[-1]
        route = fake_route("https://public.example/", navigation=True, frame=page.main_frame,
                           location="http://169.254.169.254/admin" if case == "redirect" else None)
        if case != "redirect":
            route.fetch.side_effect = PlaywrightTimeout("timeout") if case == "timeout" else Error("transport")

        async def navigate(*args, **kwargs):
            await context.route.await_args.args[1](route)
            return page.goto.return_value

        page.goto.side_effect = navigate
        return context

    f.browser.new_context.side_effect = context_with_request
    with pytest.raises(expected):
        await f.service.capture("https://public.example/")
    route.abort.assert_awaited_once()
    route.fulfill.assert_not_awaited()
    route.fetch.assert_awaited_once_with(url="https://public.example/", max_redirects=0,
                                        timeout=settings.browser_timeout_ms)
    f.pages[0].screenshot.assert_not_awaited()
    f.contexts[0].close.assert_awaited_once()
    await f.service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("url,navigation,failure", [
    ("https://public.example/image.png", False, "timeout"),
    ("https://cdn.example/script.js", False, "transport"),
    ("http://127.0.0.1/private", True, None),
    ("http://169.254.169.254/private", True, None),
    ("http://169.254.169.254/private.png", False, None),
    ("http://printer.local/script.js", False, None),
])
async def test_non_main_request_failure_preserves_capture(browser_fake, url, navigation, failure):
    f = browser_fake
    original = f.browser.new_context.side_effect
    route = fake_route(url, navigation=navigation, frame=object())
    if failure:
        route.fetch.side_effect = PlaywrightTimeout("timeout") if failure == "timeout" else Error("transport")

    async def context_with_request(**kwargs):
        context = await original(**kwargs)
        page = f.pages[-1]
        assert route.request.frame != page.main_frame

        async def navigate(*args, **kwargs):
            await context.route.await_args.args[1](route)
            return page.goto.return_value

        page.goto.side_effect = navigate
        return context

    f.browser.new_context.side_effect = context_with_request
    result = await f.service.capture("https://public.example/")
    assert result.title == "Title" and result.extracted_text == "Visible content"
    assert result.screenshot.startswith(b"\x89PNG")
    route.abort.assert_awaited_once()
    route.fulfill.assert_not_awaited()
    if failure:
        route.fetch.assert_awaited_once()
    else:
        route.fetch.assert_not_awaited()
    f.contexts[0].close.assert_awaited_once()
    await f.service.close()


@pytest.mark.asyncio
async def test_routing_private_subrequest_redirect_and_dns_rebinding(browser_fake):
    f = browser_fake
    await f.service.capture("https://public.example/")
    handler = f.contexts[0].route.await_args.args[1]
    private = fake_route("http://127.0.0.1/private.png")
    await handler(private)
    private.abort.assert_awaited_once()
    private.fetch.assert_not_awaited()
    private.fulfill.assert_not_awaited()
    redirect = fake_route("https://public.example/", location="http://169.254.169.254/admin", navigation=True,
                          frame=f.pages[0].main_frame)
    await handler(redirect)
    redirect.abort.assert_awaited_once()
    redirect.fulfill.assert_not_awaited()
    redirect.fetch.assert_awaited_once_with(url="https://public.example/", max_redirects=0, timeout=settings.browser_timeout_ms)
    f.resolver.return_value = ["127.0.0.1"]
    rebound = fake_route("https://public.example/image.png")
    await handler(rebound)
    rebound.abort.assert_awaited_once()
    rebound.fetch.assert_not_awaited()
    await f.service.close()


@pytest.mark.asyncio
async def test_initial_blocked_without_browser(browser_fake):
    with pytest.raises(BlockedURL):
        await browser_fake.service.capture("http://127.0.0.1/")
    browser_fake.factory.assert_not_called()


@pytest.mark.asyncio
async def test_unavailable_browser_stops_runtime(browser_fake):
    f = browser_fake
    f.runtime.chromium.launch.side_effect = Error("No executable")
    with pytest.raises(BrowserUnavailableError):
        await f.service.capture("https://public.example/")
    f.runtime.stop.assert_awaited_once()
    assert not f.service.ready


@pytest.mark.asyncio
async def test_screenshot_failure_context_cleanup(browser_fake):
    f = browser_fake
    original = f.browser.new_context.side_effect
    async def context(**kwargs):
        result = await original(**kwargs)
        f.pages[-1].screenshot.side_effect = Error("Screenshot failed")
        return result
    f.browser.new_context.side_effect = context
    with pytest.raises(RuntimeError):
        await f.service.capture("https://public.example/")
    f.contexts[0].close.assert_awaited_once()
    await f.service.close()


@pytest.mark.asyncio
async def test_websocket_blocker_and_dialog_popup_handlers(browser_fake):
    f = browser_fake
    await f.service.capture("https://public.example/")
    context = f.contexts[0]
    ws = SimpleNamespace(close=Mock())
    context.route_web_socket.await_args.args[1](ws)
    ws.close.assert_called_once_with()
    popup = SimpleNamespace(close=AsyncMock())
    await context.on.call_args.args[1](popup)
    popup.close.assert_awaited_once()
    dialog = SimpleNamespace(dismiss=AsyncMock())
    await f.pages[0].on.call_args.args[1](dialog)
    dialog.dismiss.assert_awaited_once()
    f.pages[0].close.assert_awaited_once()
    await f.service.close()


@pytest.mark.asyncio
async def test_cancellation_during_context_allocation_closes_it(browser_fake):
    f = browser_fake
    original = f.browser.new_context.side_effect
    entered, release = asyncio.Event(), asyncio.Event()
    async def allocate(**kwargs):
        context = await original(**kwargs)
        entered.set()
        await release.wait()
        return context
    f.browser.new_context.side_effect = allocate
    task = asyncio.create_task(f.service.capture("https://public.example/"))
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    f.contexts[0].close.assert_awaited_once()
    assert not f.service._contexts
    await f.service.close()


@pytest.mark.asyncio
async def test_browser_crash_recovery(browser_fake):
    f = browser_fake
    await f.service.capture("https://public.example/")
    f.browser.is_connected.return_value = False
    await f.service.capture("https://public.example/")
    assert f.runtime.chromium.launch.await_count == 2
    f.runtime.stop.assert_awaited_once()
    await f.service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [SimpleNamespace(status=403, headers={"content-type": "text/html"}),
                                      SimpleNamespace(status=200, headers={"content-type": "application/pdf"})])
async def test_blocked_page_and_binary_navigation_controlled(browser_fake, response):
    f = browser_fake
    original = f.browser.new_context.side_effect
    async def context(**kwargs):
        value = await original(**kwargs)
        f.pages[-1].goto.return_value = response
        return value
    f.browser.new_context.side_effect = context
    with pytest.raises(RuntimeError):
        await f.service.capture("https://public.example/")
    f.contexts[0].close.assert_awaited_once()
    f.pages[0].screenshot.assert_not_awaited()
    await f.service.close()
