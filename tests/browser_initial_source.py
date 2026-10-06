"""Mocked onboarding regression, also invoked by the full browser smoke.

python -m tests.browser_initial_source
Uses the smoke harness's temporary DB, fake AI/capture and no .env.
"""
import json
import hashlib

from tests import browser_ui_smoke as smoke


def verify_warm_module_cache(browser):
    """Use a real HTTP cache, without Playwright routing (which disables cache)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from urllib.parse import urlsplit
    from playwright.sync_api import expect

    old_entry = b"document.body.dataset.cachedEntry = 'old-profile-only';"
    old_html = b'<!doctype html><body><script type="module" src="/static/js/app.js"></script></body>'
    served, current = [], False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            served.append(self.path)
            path = urlsplit(self.path).path
            if path == '/':
                body = (smoke.ROOT / 'frontend/index.html').read_bytes() if current else old_html
                mime, cache = 'text/html; charset=utf-8', 'no-store'
            elif path == '/api/v2/competitors':
                body, mime, cache = b'[]', 'application/json', 'no-store'
            elif path == '/static/js/app.js' and self.path == path and not current:
                body, mime, cache = old_entry, 'text/javascript', 'public, max-age=3600'
            elif path.startswith('/static/'):
                asset = (smoke.ROOT / 'frontend' / path.removeprefix('/static/')).resolve()
                assert asset.is_relative_to(smoke.ROOT / 'frontend') and asset.is_file()
                body = asset.read_bytes()
                mime = 'text/javascript' if asset.suffix == '.js' else 'text/css'
                cache = 'no-cache, max-age=0, must-revalidate'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', cache)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = browser.new_context()
    page = context.new_page()
    try:
        url = f'http://127.0.0.1:{server.server_port}'
        page.goto(url)
        page.wait_for_function("document.body.dataset.cachedEntry === 'old-profile-only'")
        # A second navigation really reuses the old asset from the HTTP cache.
        page.goto(url)
        page.wait_for_function("document.body.dataset.cachedEntry === 'old-profile-only'")
        assert served.count('/static/js/app.js') == 1
        current = True
        page.goto(url)
        page.wait_for_load_state('networkidle')
        page.get_by_role('button', name='Добавить конкурента', exact=True).click()
        expect(page.get_by_label('Первый источник (необязательно)', exact=True)).to_be_visible()
        assert page.locator('#editor-form [name=website_url]').count() == 0
        assert '/static/js/app.js?v=capture-recovery-20261006' in served
        assert served.count('/static/js/app.js') == 1
        print('WARM_BROWSER_CACHE: old module reused before update; versioned entry loads current real form PASS')
    finally:
        context.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_onboarding(browser, origin, png, captures):
    from playwright.sync_api import expect
    from backend.services.ai_service import ai_service, AIProviderError

    verify_warm_module_cache(browser)
    artifacts = smoke.ROOT / '.pytest-temp/onboarding-artifacts'
    artifacts.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 900})
    page = context.new_page()
    page.set_default_timeout(12000)
    requests, errors, external, unexpected_http, console_errors, loaded_modules = [], [], [], [], [], []
    expected_http = set()
    page.on('request', lambda request: requests.append((request.method, request.url)))
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('response', lambda response: unexpected_http.append((response.url, response.status))
            if response.status >= 400 and (response.url, response.status) not in expected_http else None)
    page.on('console', lambda msg: console_errors.append((msg.text, msg.location)) if msg.type == 'error' else None)

    def audit_module(response):
        from urllib.parse import urlsplit
        path = urlsplit(response.url).path
        if response.status == 200 and path.startswith('/static/js/') and path.endswith('.js'):
            loaded = response.body()
            disk = (smoke.ROOT / 'frontend' / path.removeprefix('/static/')).read_bytes()
            assert loaded == disk, 'Running UI loaded a stale module: ' + path
            loaded_modules.append((response.url, hashlib.sha256(loaded).hexdigest()))

    page.on('response', audit_module)

    def guard(route):
        if not route.request.url.startswith(origin + '/'):
            external.append(route.request.url)
            route.abort()
        else:
            route.continue_()

    context.route('**/*', guard)

    def settle():
        page.wait_for_load_state('networkidle')
        page.wait_for_function("async () => !(await import('/static/js/state.js')).state.loading.size")

    def state_value(key):
        return page.evaluate("async key => (await import('/static/js/state.js')).state[key]", key)

    def detail(cid):
        response = context.request.get(f'{origin}/api/v2/competitors/{cid}')
        assert response.status == 200
        return response.json()

    def source(sid):
        response = context.request.get(f'{origin}/api/v2/sources/{sid}')
        assert response.status == 200
        return response.json()

    def open_create(name, value=''):
        page.locator('#new-competitor').click()
        expect(page.locator('#form-submit')).to_have_text('Сохранить')
        page.get_by_label('Название', exact=True).fill(name)
        page.get_by_label('Первый источник (необязательно)', exact=True).fill(value)
        expect(page.locator('#field-initial_source')).to_have_attribute('aria-describedby', 'initial-source-hint')
        expect(page.locator('#initial-source-hint')).to_have_text(
            'Вставьте URL сайта или текст. Источник будет добавлен и проанализирован автоматически.')

    def finish():
        expect(page.locator('#editor-dialog')).not_to_be_visible()
        settle()
        return state_value('activeCompetitorId')

    def creates():
        return sum(method == 'POST' and url == origin + '/api/v2/competitors' for method, url in requests)

    def capture(name):
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
        assert page.locator('#editor-dialog').evaluate('dialog => dialog.scrollWidth <= dialog.clientWidth'), name
        page.screenshot(path=str(artifacts / f'{name}.png'))

    try:
        page.goto(origin)
        settle()
        expect(page.locator('script[type=module]')).to_have_attribute(
            'src', '/static/js/app.js?v=capture-recovery-20261006')
        assert any(url.endswith('/static/js/app.js?v=capture-recovery-20261006') for url, _ in loaded_modules)
        # Exact human scenario: use only the real creation dialog and its optional field.
        page.get_by_role('button', name='Добавить конкурента', exact=True).click()
        page.get_by_label('Название', exact=True).fill('Cohere')
        page.get_by_label('Первый источник (необязательно)', exact=True).fill('https://cohere.com/')
        assert page.locator('#editor-form [name=website_url]').count() == 0
        capture('cohere-form-desktop')
        page.set_viewport_size({'width': 390, 'height': 844})
        capture('cohere-form-mobile')
        page.set_viewport_size({'width': 1440, 'height': 900})
        with page.expect_response(lambda response: response.request.method == 'POST'
                                  and response.url == origin + '/api/v2/competitors') as created_response, \
             page.expect_response(lambda response: response.request.method == 'POST'
                                  and response.url.endswith('/sources/url')) as source_response:
            page.get_by_role('button', name='Сохранить', exact=True).click()
        cid = finish()
        assert created_response.value.status == 201 and source_response.value.status == 201
        assert created_response.value.request.post_data_json['name'] == 'Cohere'
        assert source_response.value.request.post_data_json['url'] == 'https://cohere.com/'
        assert creates() == 1
        assert requests.count(('POST', origin + '/api/v2/competitors/' + cid + '/sources/url')) == 1
        assert created_response.value.json()['id'] == cid
        sources = detail(cid)['sources']
        assert detail(cid)['website_url'] == 'https://cohere.com/'
        assert created_response.value.request.post_data_json['website_url'] == 'https://cohere.com/'
        assert len(sources) == 1 and sources[0]['source_type'] == 'url'
        assert sources[0]['url'] == 'https://cohere.com/'
        assert state_value('activeSourceId') == sources[0]['id']
        expect(page.locator('#source-list .source-card')).to_have_count(1)
        expect(page.locator('#source-list')).to_contain_text('cohere.com')
        expect(page.locator('#source-preview')).to_contain_text('https://cohere.com/')
        expect(page.locator('.summary')).to_be_visible()
        page.screenshot(path=str(artifacts / 'cohere-result-desktop.png'))
        print('EXACT_COHERE_FORM: POST /competitors 201 -> POST /competitors/' + cid +
              '/sources/url 201; url=https://cohere.com/; URL sources=1; active source=' + sources[0]['id'])
        print('CURRENT_BROWSER_MODULES: ' + json.dumps(loaded_modules))
        (artifacts / 'cohere-network.json').write_text(json.dumps({
            'environment': 'temporary DB; deterministic AI and capture mocks; real form',
            'competitor_post': {'url': created_response.value.url, 'status': created_response.value.status,
                                'payload': created_response.value.request.post_data_json, 'id': cid},
            'source_post': {'url': source_response.value.url, 'status': source_response.value.status,
                            'payload': source_response.value.request.post_data_json, 'id': sources[0]['id']},
            'url_source_count': len(sources), 'active_source_id': state_value('activeSourceId'),
            'loaded_modules_sha256': loaded_modules,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('.report-context').scroll_into_view_if_needed()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(artifacts / 'cohere-result-mobile.png'))
        page.set_viewport_size({'width': 1440, 'height': 900})
        # A: name only; the existing source action remains enabled.
        open_create('Onboarding name only')
        page.locator('#form-submit').click()
        cid = finish()
        assert detail(cid)['sources'] == []
        expect(page.locator('#add-source')).to_be_enabled()
        page.locator('#add-source').click()
        page.get_by_label('Текст (10–30 000 символов)', exact=True).fill('Первый источник добавлен вручную.')
        page.locator('#form-submit').click()
        finish()
        assert len(detail(cid)['sources']) == 1

        # B: automatic URL, then existing Add source creates a second Text source.
        open_create('Onboarding URL', 'https://public.example/onboarding')
        page.locator('#form-submit').click()
        cid = finish()
        first = detail(cid)['sources'][0]
        assert first['source_type'] == 'url' and state_value('activeSourceId') == first['id']
        expect(page.locator('.summary')).to_be_visible()
        original = source(first['id'])
        page.locator('#add-source').click()
        page.get_by_label('Название источника', exact=True).fill('Второй источник')
        page.get_by_label('Текст (10–30 000 символов)', exact=True).fill('Дополнительный текст о конкуренте.')
        page.locator('#form-submit').click()
        finish()
        sources = detail(cid)['sources']
        assert len(sources) == 2 and len({item['id'] for item in sources}) == 2
        assert source(first['id']) == original, 'Initial source overwritten'
        second = next(item for item in sources if item['id'] != first['id'])
        assert second['source_type'] == 'text' and state_value('activeSourceId') == second['id']
        for item in (first, second):
            page.locator('.source-card').filter(has=page.locator('strong', has_text=item['label'])).click()
            settle()
            assert state_value('activeSourceId') == item['id']
            expect(page.locator('.summary')).to_be_visible()
        # Additional URL and file options are still the existing source dialog.
        page.locator('#add-source').click()
        page.get_by_role('button', name='URL', exact=True).click()
        page.get_by_label('URL страницы', exact=True).fill('https://public.example/additional')
        page.locator('#form-submit').click()
        finish()
        page.locator('#add-source').click()
        page.get_by_role('button', name='Изображение / PDF', exact=True).click()
        page.locator('input[type=file]').set_input_files({'name': 'additional.png', 'mimeType': 'image/png', 'buffer': png})
        page.locator('#form-submit').click()
        finish()
        import pymupdf
        with pymupdf.open() as document:
            document.new_page().insert_text((50, 50), 'Additional PDF source')
            pdf = document.tobytes()
        page.locator('#add-source').click()
        page.get_by_role('button', name='Изображение / PDF', exact=True).click()
        page.locator('input[type=file]').set_input_files({'name': 'additional.pdf', 'mimeType': 'application/pdf', 'buffer': pdf})
        page.locator('#form-submit').click()
        finish()
        sources = detail(cid)['sources']
        assert len(sources) == 5 and len({item['id'] for item in sources}) == 5
        assert {item['source_type'] for item in sources} == {'text', 'url', 'image', 'pdf'}
        assert source(first['id']) == original

        # Real form -> real API -> capture failure; only service boundaries are mocked.
        from backend.services.browser_service import browser_service, BrowserCaptureError, BrowserTimeoutError
        original_capture = browser_service.capture
        original_analyze = ai_service.analyze_source
        blocked = True
        ai_calls = []
        failure = BrowserCaptureError('Navigation did not produce an HTML page')
        async def failing_capture(url):
            if blocked and url == 'https://www.perplexity.ai/':
                raise failure
            return await original_capture(url)
        async def counted_analyze(prepared):
            ai_calls.append(prepared.source_type)
            return await original_analyze(prepared)
        def capture_response(route):
            response = route.fetch()
            if response.status >= 400:
                assert response.status in (500, 504)
                assert response.json()['error']['code'] in ('BROWSER_ERROR', 'BROWSER_TIMEOUT')
                expected_http.add((route.request.url, response.status))
            route.fulfill(response=response)
        browser_service.capture = failing_capture
        ai_service.analyze_source = counted_analyze
        page.route('**/sources/url', capture_response)
        evidence = []
        try:
            for label, width, height, recovery in [('desktop', 1440, 900, 'retry'),
                                                  ('mobile', 390, 844, 'image'),
                                                  ('other', 1440, 900, 'text')]:
                page.set_viewport_size({'width': width, 'height': height})
                blocked = True
                failure = BrowserTimeoutError('timeout') if recovery == 'text' else BrowserCaptureError('HTTP 403')
                before_creates, before_ai = creates(), len(ai_calls)
                open_create('Perplexity AI', 'https://www.perplexity.ai/')
                capture('perplexity-' + label + '-form')
                with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/sources/url')) as failed:
                    page.locator('#form-submit').click()
                settle()
                cid = state_value('activeCompetitorId')
                assert creates() == before_creates + 1
                assert detail(cid)['name'] == 'Perplexity AI'
                assert detail(cid)['website_url'] == 'https://www.perplexity.ai/'
                assert detail(cid)['sources'] == [] and len(ai_calls) == before_ai
                expect(page.locator('#field-initial_source')).to_have_value('https://www.perplexity.ai/')
                expect(page.locator('#form-error')).to_contain_text('Конкурент создан. Не удалось автоматически получить страницу.')
                assert 'Browser capture' not in page.locator('#form-error').inner_text()
                expect(page.locator('#source-recovery')).to_contain_text('https://www.perplexity.ai/')
                expect(page.get_by_role('button', name='Повторить получение', exact=True)).to_be_enabled()
                expect(page.get_by_role('button', name='Загрузить скриншот', exact=True)).to_be_enabled()
                expect(page.get_by_role('button', name='Добавить другой источник', exact=True)).to_be_enabled()
                page.locator('#form-submit').scroll_into_view_if_needed()
                capture('perplexity-' + label + '-error')
                if recovery == 'retry':
                    blocked = False
                    page.get_by_role('button', name='Повторить получение', exact=True).click()
                    page.locator('#editor-form').evaluate("form => form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}))")
                else:
                    page.get_by_role('button', name='Загрузить скриншот' if recovery == 'image' else 'Добавить другой источник', exact=True).click()
                    expect(page.locator('#editor-title')).to_have_text('Добавить источник')
                    expect(page.locator('#editor-fields')).to_contain_text('https://www.perplexity.ai/')
                    if recovery == 'image':
                        page.locator('input[type=file]').set_input_files({'name': 'perplexity.png', 'mimeType': 'image/png', 'buffer': png})
                    else:
                        page.get_by_label('Текст (10–30 000 символов)', exact=True).fill('Другой источник о Perplexity AI.')
                    page.locator('#form-submit').click()
                assert finish() == cid
                entries = detail(cid)['sources']
                assert len(entries) == 1 and entries[0]['source_type'] == {'retry': 'url', 'image': 'image', 'text': 'text'}[recovery]
                assert creates() == before_creates + 1 and len(ai_calls) == before_ai + 1
                assert detail(cid)['website_url'] == 'https://www.perplexity.ai/'
                assert state_value('activeSourceId') == entries[0]['id']
                expect(page.locator('#source-list .source-card')).to_have_count(1)
                expect(page.locator('.summary')).to_be_visible()
                assert requests.count(('POST', origin + '/api/v2/competitors/' + cid + '/sources/url')) == (2 if recovery == 'retry' else 1)
                page.locator('#analysis-pane').scroll_into_view_if_needed()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(artifacts / f'perplexity-{label}-result.png'))
                evidence.append({'competitor_id': cid, 'failed_url': failed.value.request.post_data_json['url'],
                    'failure_status': failed.value.status, 'error_code': failed.value.json()['error']['code'],
                    'recovery': recovery, 'source_id': entries[0]['id'], 'final_sources': len(entries),
                    'competitor_posts': creates() - before_creates, 'mock_ai_calls': len(ai_calls) - before_ai})
                # Ordinary Add source remains available after every recovery.
                page.locator('#add-source').click()
                page.get_by_label('Текст (10–30 000 символов)', exact=True).fill('Дополнительный ручной источник.')
                page.locator('#form-submit').click()
                finish()
                assert len(detail(cid)['sources']) == 2
                assert any(s['id'] == entries[0]['id'] for s in detail(cid)['sources'])
            (artifacts / 'capture-recovery-network.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
            print('CAPTURE_RECOVERY: real Perplexity form/API; failure with zero AI; retry/Image/text; no duplicate competitor/source; manual additions PASS')
        finally:
            browser_service.capture = original_capture
            ai_service.analyze_source = original_analyze
            page.unroute('**/sources/url', capture_response)

        # C: plain text, including whitespace trimming, uses text ingestion.
        text = 'Mistral AI develops generative AI models and services...'
        open_create('Onboarding text', '  ' + text + '  ')
        page.locator('#form-submit').click()
        cid = finish()
        sid = detail(cid)['sources'][0]['id']
        assert source(sid)['source']['source_type'] == 'text'
        assert source(sid)['snapshots'][0]['extracted_text'] == text
        assert state_value('activeSourceId') == sid

        # Invalid URL never becomes a text analysis; retry does not create a competitor.
        before_creates = creates()
        before_captures = len(captures)
        open_create('Onboarding invalid URL', 'https://')
        page.locator('#form-submit').click()
        expect(page.locator('#form-error')).to_contain_text('Конкурент создан.')
        settle()
        cid = state_value('activeCompetitorId')
        assert not detail(cid)['sources'] and len(captures) == before_captures
        page.locator('#field-initial_source').fill('https://public.example/corrected')
        page.locator('#form-submit').click()
        finish()
        assert creates() == before_creates + 1 and len(detail(cid)['sources']) == 1

        # Server SSRF validation: private literal and private DNS resolution.
        for index, value in enumerate(('http://127.0.0.1/', 'https://private-dns.example/')):
            open_create(f'Onboarding SSRF {index}', value)
            # Competitor ID is unknown until the first POST returns.
            def expect_blocked(route):
                expected_http.add((route.request.url, 400))
                route.continue_()
            page.route('**/sources/url', expect_blocked)
            before_captures = len(captures)
            page.locator('#form-submit').click()
            expect(page.locator('#form-error')).to_contain_text('Этот адрес нельзя анализировать.')
            settle()
            cid = state_value('activeCompetitorId')
            assert not detail(cid)['sources'] and len(captures) == before_captures
            page.unroute('**/sources/url', expect_blocked)
            page.locator('#form-cancel').click()

        # Real partial persistence: fake provider fails after source/snapshot commit.
        analyze = ai_service.analyze_source
        async def fail(_prepared):
            raise AIProviderError('injected onboarding failure')
        ai_service.analyze_source = fail
        open_create('Onboarding persisted failure', 'Текст сохраняется до ошибки анализа.')
        def expect_provider_error(route):
            expected_http.add((route.request.url, 502))
            route.continue_()
        page.route('**/sources/text', expect_provider_error)
        before_creates = creates()
        page.locator('#form-submit').click()
        expect(page.locator('#form-error')).to_contain_text('Конкурент создан.')
        settle()
        ai_service.analyze_source = analyze
        page.unroute('**/sources/text', expect_provider_error)
        cid = state_value('activeCompetitorId')
        saved = detail(cid)['sources']
        assert len(saved) == 1 and not source(saved[0]['id'])['analyses']
        expect(page.locator('#field-initial_source')).to_have_value('Текст сохраняется до ошибки анализа.')
        expect(page.locator('#form-submit')).to_have_text('Повторить анализ')
        page.locator('#form-submit').click()
        finish()
        assert creates() == before_creates + 1
        assert [item['id'] for item in detail(cid)['sources']] == [saved[0]['id']]
        assert len(source(saved[0]['id'])['analyses']) == 1

        # Lost success response: reconciliation opens persisted result, never duplicates.
        open_create('Onboarding lost response', 'Источник с потерянным ответом сервера.')
        def lost_response(route):
            response = route.fetch()
            assert response.status == 201
            route.fulfill(status=201, content_type='application/json', body='null')
        page.route('**/sources/text', lost_response)
        page.locator('#form-submit').click()
        expect(page.locator('#form-error')).to_contain_text('Конкурент создан.')
        settle()
        cid = state_value('activeCompetitorId')
        saved = detail(cid)['sources']
        page.unroute('**/sources/text', lost_response)
        page.locator('#form-submit').click()
        finish()
        assert [item['id'] for item in detail(cid)['sources']] == [saved[0]['id']]
        assert len(source(saved[0]['id'])['analyses']) == 1

        # Unknown outcome with no visible source blocks another creation request.
        open_create('Onboarding unknown outcome', 'Данные с неопределённым результатом запроса.')
        def unknown_response(route):
            route.fulfill(status=201, content_type='application/json', body='null')
        page.route('**/sources/text', unknown_response)
        before_creates = creates()
        before_sources = sum(method == 'POST' and '/sources/text' in url for method, url in requests)
        page.locator('#form-submit').click()
        expect(page.locator('#form-error')).to_contain_text('Конкурент создан.')
        settle()
        cid = state_value('activeCompetitorId')
        page.unroute('**/sources/text', unknown_response)
        page.locator('#form-submit').click()
        expect(page.locator('#form-error')).to_contain_text('повторная отправка заблокирована')
        settle()
        assert creates() == before_creates + 1 and not detail(cid)['sources']
        assert sum(method == 'POST' and '/sources/text' in url for method, url in requests) == before_sources + 1
        page.locator('#form-cancel').click()

        # Desktop/mobile dialog, loading, controlled failure and corrected retry.
        for label, width, height in [('desktop', 1440, 900), ('mobile', 390, 844)]:
            page.set_viewport_size({'width': width, 'height': height})
            open_create(f'Onboarding visual {label}', text)
            capture(label + '-dialog')
            pending = []
            def hold(route):
                pending.append(route)
            page.route('**/sources/text', hold)
            before_creates = creates()
            page.locator('#form-submit').click()
            expect(page.locator('#form-status')).to_contain_text('Конкурент создан. Источник отправляется')
            expect(page.locator('#form-submit')).to_be_disabled()
            page.locator('#editor-form').evaluate("form => form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}))")
            page.wait_for_timeout(100)
            assert len(pending) == 1, 'Duplicate source during loading'
            page.locator('#form-submit').scroll_into_view_if_needed()
            capture(label + '-loading')
            expected_http.add((pending[0].request.url, 400))
            pending[0].fulfill(status=400, content_type='application/json', body=json.dumps({
                'error': {'code': 'INVALID_INPUT', 'message': 'Исправьте данные источника.', 'details': None}}))
            page.unroute('**/sources/text', hold)
            expect(page.locator('#form-error')).to_contain_text('Конкурент создан.')
            settle()
            cid = state_value('activeCompetitorId')
            assert not detail(cid)['sources']
            expect(page.locator('#field-initial_source')).to_have_value(text)
            page.locator('#form-error').scroll_into_view_if_needed()
            page.locator('#form-submit').scroll_into_view_if_needed()
            capture(label + '-error')
            page.locator('#field-initial_source').fill(text + ' Corrected.')
            page.locator('#form-submit').click()
            finish()
            assert creates() == before_creates + 1 and len(detail(cid)['sources']) == 1
            page.locator('#analysis-pane').scroll_into_view_if_needed()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(artifacts / f'{label}-result.png'))

        assert not any('/aggregate-analysis' in url or '/comparisons' in url for method, url in requests if method == 'POST')
        assert not errors and not external and not unexpected_http, (errors, external, unexpected_http)
        for text, location in console_errors:
            assert any(location.get('url') == url and text ==
                       f'Failed to load resource: the server responded with a status of {status} ({ {400: "Bad Request", 500: "Internal Server Error", 502: "Bad Gateway", 504: "Gateway Timeout"}[status]})'
                       for url, status in expected_http), (text, location)
        print('INITIAL_SOURCE: name-only/URL/text/invalid URL/SSRF literal+DNS/partial failure/retry/no duplicates PASS')
        print('INITIAL_SOURCE + EXISTING ADD SOURCE: URL then Text/URL/Image/PDF; first source unchanged PASS')
        print('ONBOARDING: desktop=1440x900 mobile=390x844 labels/loading/errors/results/no overflow PASS')
        print('ONBOARDING: no aggregate/comparison; no live AI/external requests; screenshots=' + str(artifacts))
    finally:
        if 'analyze' in locals():
            ai_service.analyze_source = analyze
        context.close()


def run_browser(origin, png, captures):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            run_onboarding(browser, origin, png, captures)
        finally:
            browser.close()


if __name__ == '__main__':
    smoke.run_browser = run_browser
    smoke.main()
