"""Explicit offline Chromium Stage 8 integration: python -m tests.browser_ui_smoke.

Real FastAPI, SQLite and file workflows; only AI and browser capture are fakes.
HTTP faults/delays are scoped Playwright routes. Never loads .env or production DB.
"""
import asyncio
import json
import logging
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
from io import BytesIO

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = '<img src=x onerror=alert(1)> <script>alert(1)</script> "><svg/onload=alert(1)>'


def filesystem():
    import hashlib
    result = {}
    for directory in ('uploads', 'screenshots'):
        result[directory] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in (ROOT / 'data' / directory).rglob('*') if p.is_file()}
    db = ROOT / 'data/app.db'
    result['db'] = hashlib.sha256(db.read_bytes()).hexdigest() if db.exists() else None
    return result


def configure(runtime):
    from pydantic_settings import DotEnvSettingsSource
    DotEnvSettingsSource._read_env_files = lambda self: {}
    for key in list(os.environ):
        if key.startswith(('AI_', 'OPENAI_', 'PROXY_', 'APP_', 'API_', 'MAX_', 'BROWSER_')) or key in {
            'DATABASE_URL', 'UPLOAD_DIR', 'SCREENSHOT_DIR', 'CORS_ORIGINS', 'LOG_LEVEL',
        }:
            os.environ.pop(key, None)
    os.environ.update(DATABASE_URL=f"sqlite:///{(runtime / 'app.db').as_posix()}",
                      UPLOAD_DIR=str(runtime / 'uploads'), SCREENSHOT_DIR=str(runtime / 'screenshots'),
                      LOG_LEVEL='ERROR')


def analysis_result():
    from backend.models.analysis import CompetitorAnalysis
    return CompetitorAnalysis.model_validate({
        'executive_summary': f'Проверяемое предложение для команд. {PAYLOAD}',
        'positioning': 'Автоматизация для команд', 'target_audience': ['Команды'],
        'value_propositions': ['Экономия времени'], 'differentiators': ['Единое пространство'],
        'strengths': ['Ясное предложение'], 'gaps': ['Мало доказательств'],
        'marketing_messages': ['Сократите ручную работу'],
        'scorecard': {**{key: {'score': 6, 'rationale': f'Наблюдаемое основание. {PAYLOAD}'} for key in
                          ('positioning_clarity', 'value_proposition', 'trust', 'cta_strength')},
                      'visual_consistency': {'score': 5, 'rationale': 'Ограниченные визуальные данные'},
                      'ux_clarity': None},
        'evidence': [{'category': 'Позиционирование', 'finding': PAYLOAD, 'evidence': PAYLOAD,
                      'source_hint': PAYLOAD, 'confidence': 'medium'}],
        'opportunities': ['Добавить кейсы'], 'recommended_actions': ['Показать результаты клиентов'],
        'limitations': [f'Ограниченная доказательная база. {PAYLOAD}'],
    })


def fake_boundaries():
    from backend.services.ai_service import ai_service
    from backend.services.browser_service import browser_service, BrowserCapture
    from backend.models.analysis import ComparisonResult
    from PIL import Image

    async def analyze(_prepared):
        await asyncio.sleep(.12)
        return analysis_result()

    async def compare(prepared):
        return ComparisonResult.model_validate({
            'executive_summary': 'Сравнение предоставленных профилей',
            'competitors': [{'competitor_id': p.competitor_id, 'competitor_name': p.competitor_name,
                            'positioning': 'Автоматизация', 'strengths': ['Ясность'], 'gaps': ['Мало данных'],
                            **{key: 6 for key in ('positioning_clarity', 'value_proposition', 'trust', 'cta_strength')}}
                           for p in prepared.competitors],
            'shared_patterns': ['Автоматизация'], 'meaningful_differences': ['Разная полнота данных'],
            'market_gaps': ['Кейсы'], 'opportunities': ['Доказательства'],
            'limitations': [f'Покрытие evidence различается. {PAYLOAD}'],
        })

    captures = []
    buffer = BytesIO()
    Image.new('RGB', (160, 100), '#22425a').save(buffer, format='PNG')

    async def capture(url):
        captures.append(url)
        return BrowserCapture(url, url + 'final', f'Страница {len(captures)}', 'Описание страницы',
                              f'Контент snapshot {len(captures)}. {PAYLOAD}', buffer.getvalue(), {'text_truncated': True})

    ai_service.analyze_source = analyze
    ai_service.aggregate_competitor = analyze
    ai_service.compare_competitors = compare
    browser_service.capture = capture
    return buffer.getvalue(), captures


def run_browser(origin, png, captures):
    from playwright.sync_api import sync_playwright, expect
    import pymupdf

    artifacts = ROOT / '.pytest-temp/ui-artifacts'
    artifacts.mkdir(parents=True, exist_ok=True)
    errors, console_errors, external, failed, bad_assets, requests = [], [], [], [], [], []
    expected_http_errors = set()
    expected_http_responses = []
    phase = 'startup'

    def audit_response(response):
        if response.status >= 400:
            signature = (response.request.method, response.url, response.status)
            if signature in expected_http_errors:
                expected_http_responses.append(signature)
            else:
                bad_assets.append((response.status, response.url))

    def audit_console(msg):
        if msg.type == 'error':
            # Always record, including expected HTTP 4xx scopes. Never mute JS errors.
            console_errors.append((msg.text, msg.location, frozenset(expected_http_errors)))

    def expected_http_diagnostic(entry):
        text, location, scope = entry
        signature = ('POST', location.get('url'), 400)
        return (text == 'Failed to load resource: the server responded with a status of 400 (Bad Request)'
                and signature in scope and signature in expected_http_responses
                and location.get('lineNumber') == 0 and location.get('columnNumber') == 0)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 900})
        page = context.new_page()
        page.set_default_timeout(12000)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', audit_console)
        page.on('requestfailed', lambda req: failed.append((phase, req.method, req.url, req.failure)))
        page.on('response', audit_response)
        page.on('request', lambda req: requests.append((req.method, req.url, req.headers.get('content-type', ''))))

        def guard(route):
            if not route.request.url.startswith(origin + '/'):
                external.append(route.request.url)
                route.abort()
            else:
                route.continue_()

        context.route('**/*', guard)
        page.goto(origin + '/')
        expect(page.get_by_text('Пока нет конкурентов', exact=True)).to_be_visible()
        expect(page.get_by_text('Конкурент не выбран', exact=True)).to_be_visible()

        def settle():
            page.wait_for_function("async () => { const {state} = await import('/static/js/state.js'); return !state.loading.size; }")
            # State flags settle before background card hydration and routed bodies.
            page.wait_for_load_state('networkidle')
            page.wait_for_function("async () => { const {state} = await import('/static/js/state.js'); return !state.loading.size; }")

        def create(name):
            page.locator('#new-competitor').click()
            page.get_by_label('Название', exact=True).fill(name)
            page.locator('#form-submit').click()
            expect(page.locator('#editor-dialog')).not_to_be_visible()
            settle()
            expect(page.locator('#competitor-name')).to_have_text(name)
            return page.evaluate("async () => (await import('/static/js/state.js')).state.activeCompetitorId")

        def text_source(label, text=PAYLOAD):
            page.locator('#add-source').click()
            page.get_by_label('Название источника', exact=True).fill(label)
            page.get_by_label('Текст (10–30 000 символов)', exact=True).fill(text)
            page.locator('#form-submit').click()
            expect(page.locator('#editor-dialog')).not_to_be_visible()
            settle()
            expect(page.locator('#source-preview h3')).to_have_text(label)
            expect(page.locator('.summary')).to_contain_text(PAYLOAD)
            return page.evaluate("async () => (await import('/static/js/state.js')).state.activeSourceId")

        alpha = create(PAYLOAD)
        expect(page.get_by_text('У конкурента нет источников', exact=True)).to_be_visible()
        # Real backend prerequisite failure: aggregate needs current source analyses,
        # not an already-existing aggregate. Check message, loading cleanup and retry.
        phase = 'expected aggregate error'
        aggregate_url = f'{origin}/api/v2/competitors/{alpha}/aggregate-analysis'
        expected_http_errors.add(('POST', aggregate_url, 400))
        with page.expect_response(lambda response: response.request.method == 'POST' and response.url == aggregate_url) as rejected:
            page.locator('#aggregate').click()
        assert rejected.value.status == 400
        assert rejected.value.json()['error']['code'] == 'ANALYSIS_DATA_NOT_READY'
        settle()
        expect(page.locator('#workspace-error')).to_have_text(
            'Для сводного анализа сначала нужен хотя бы один анализ текущего snapshot источника. Добавьте источник или нажмите «Повторить анализ».')
        expect(page.locator('#workspace-error')).not_to_contain_text('Для выбранных конкурентов')
        expect(page.locator('#aggregate')).to_be_enabled()
        assert page.locator('.summary').count() == 0
        expected_http_errors.remove(('POST', aggregate_url, 400))
        phase = 'core workflow'
        page.locator('#edit-competitor').click()
        page.get_by_label('Ниша (необязательно)', exact=True).fill('Автоматизация')
        page.get_by_label('Заметки (необязательно)', exact=True).fill(PAYLOAD)
        page.locator('#form-submit').click()
        expect(page.locator('#editor-dialog')).not_to_be_visible()
        settle()
        expect(page.locator('#competitor-description')).to_have_text('Автоматизация')

        # Actual source creation delayed at HTTP boundary; duplicate click cannot issue a second request.
        async_text_url = f'{origin}/api/v2/competitors/{alpha}/sources/text'
        mutations = []
        def delayed_text(route):
            mutations.append(route.request.method)
            result = route.fetch()
            sleep(.6)
            route.fulfill(response=result)
        page.route(async_text_url, delayed_text)
        page.locator('#add-source').click()
        page.get_by_label('Название источника', exact=True).fill(PAYLOAD)
        page.get_by_label('Текст (10–30 000 символов)', exact=True).fill(PAYLOAD)
        page.locator('#form-submit').click()
        expect(page.locator('#form-submit')).to_be_disabled()
        expect(page.locator('#form-status')).to_contain_text('сервер подготовит')
        page.locator('#form-submit').evaluate('button => button.click()')
        expect(page.locator('#editor-dialog')).not_to_be_visible()
        settle()
        assert mutations == ['POST'], mutations
        page.unroute(async_text_url, delayed_text)
        text_id = page.evaluate("async () => (await import('/static/js/state.js')).state.activeSourceId")
        expect(page.locator('#source-preview pre')).to_have_text(PAYLOAD)
        assert page.get_by_role('button', name='Обновить страницу', exact=True).count() == 0
        expect(page.locator('.scorecard')).to_contain_text('6/10')
        expect(page.locator('.report-context .mode-badge')).to_have_text('Анализ отдельного источника')
        expect(page.locator('.report-subtitle')).to_contain_text(PAYLOAD)
        page.locator('.scorecard details summary').first.focus()
        page.keyboard.press('Enter')
        expect(page.locator('.scorecard .score p').first).to_be_visible()
        expect(page.locator('.scorecard .score p').first).to_contain_text('Наблюдаемое основание.')
        page.get_by_role('button', name='Доказательства', exact=True).click()
        expect(page.locator('.analysis-body')).to_contain_text(PAYLOAD)
        page.get_by_role('button', name='Действия', exact=True).click()
        expect(page.locator('.analysis-body')).to_contain_text('Показать результаты клиентов')
        page.get_by_role('button', name='Ограничения', exact=True).click()
        expect(page.locator('.analysis-body')).to_contain_text('Ограниченная доказательная база')
        page.get_by_role('button', name='Обзор', exact=True).click()
        with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/reanalyze')):
            page.get_by_role('button', name='Повторить анализ', exact=True).click()
        settle()
        expect(page.locator('#analysis-history option')).to_have_count(2)
        page.locator('#aggregate').click()
        settle()
        expect(page.locator('#analysis-content h2')).to_have_text('Сводный анализ')
        expect(page.locator('.report-context .mode-badge')).to_have_text('Сводный анализ конкурента')
        aggregate_calls = sum('/aggregate-analysis' in url for _, url, _ in requests)
        page.locator('#latest-aggregate').click()
        assert sum('/aggregate-analysis' in url for _, url, _ in requests) == aggregate_calls

        # Real native multipart uploads and backend file preparation for image and PDF.
        for filename, content, mime, expected_type in [('image.png', png, 'image/png', 'Изображение'),
                                                       ('document.pdf', None, 'application/pdf', 'PDF')]:
            if content is None:
                with pymupdf.open() as document:
                    document.new_page().insert_text((50, 50), 'Controlled PDF fixture')
                    content = document.tobytes()
            page.locator('#add-source').click()
            page.get_by_role('button', name='Изображение / PDF', exact=True).click()
            assert '.pdf' in page.locator('input[type=file]').get_attribute('accept')
            page.locator('input[type=file]').set_input_files({'name': filename, 'mimeType': mime, 'buffer': content})
            page.locator('#form-submit').click()
            expect(page.locator('#editor-dialog')).not_to_be_visible()
            settle()
            expect(page.locator('#source-list .active')).to_contain_text(expected_type)
            expect(page.locator('#source-preview')).to_contain_text(filename)
            if mime == 'image/png':
                page.wait_for_function("document.querySelector('#source-preview img')?.naturalWidth > 0")
            else:
                expect(page.locator('#source-preview')).to_contain_text('Страниц: 1')
                expect(page.locator('#source-preview')).to_contain_text('Controlled PDF fixture')

        # URL uses fake capture, never an external site. Refresh produces a new persisted snapshot.
        page.locator('#add-source').click()
        page.get_by_role('button', name='URL', exact=True).click()
        page.get_by_label('URL страницы', exact=True).fill('https://public.example/')
        page.locator('#form-submit').click()
        expect(page.locator('#editor-dialog')).not_to_be_visible()
        settle()
        url_id = page.evaluate("async () => (await import('/static/js/state.js')).state.activeSourceId")
        expect(page.locator('#source-preview')).to_contain_text('Страница 1')
        page.wait_for_function("document.querySelector('#source-preview img')?.naturalWidth > 0")
        page.get_by_role('button', name='Обновить страницу', exact=True).click()
        settle()
        expect(page.locator('#source-preview')).to_contain_text('Страница 2')
        assert len(captures) == 2
        page.get_by_role('button', name='Повторить анализ', exact=True).click()
        settle()
        assert len(captures) == 2, 'Reanalyze must not capture'
        page.locator('#analysis-history').select_option(index=2)
        expect(page.locator('#analysis-content')).to_contain_text('Исторический результат')
        page.locator('#source-list .active').click()
        settle()

        beta = create('Beta')
        beta_source = text_source('Beta text', 'Контролируемый текст для Beta')
        page.locator('#aggregate').click()
        settle()
        page.locator('#open-compare').click()
        expect(page.locator('#comparison-result')).to_contain_text('Выберите от 2 до 5')
        expect(page.locator('#compare-submit')).to_be_disabled()
        page.locator('#compare-choices input').nth(0).check()
        expect(page.locator('#compare-submit')).to_be_disabled()
        expect(page.locator('#comparison-result')).to_contain_text('Выберите ещё одного конкурента')
        page.locator('#compare-choices input').nth(1).check()
        expect(page.locator('#comparison-result')).to_contain_text('Готово к сравнению')
        expect(page.locator('#comparison-result')).not_to_contain_text('Выберите от 2 до 5')
        comparison_url = origin + '/api/v2/comparisons'
        before_comparison = sum(url == comparison_url for _, url, _ in requests)
        assert before_comparison == 0, 'Selecting competitors must never call AI'
        pending_comparison = []
        def hold_comparison(route):
            pending_comparison.append(route)
        page.route(comparison_url, hold_comparison)
        page.locator('#compare-submit').click()
        expect(page.locator('#comparison-result')).to_contain_text('Сравнение выполняется')
        expect(page.locator('#comparison-result')).to_have_attribute('aria-busy', 'true')
        expect(page.locator('#compare-submit')).to_be_disabled()
        assert all(input.is_disabled() for input in page.locator('#compare-choices input').all())
        page.locator('#compare-submit').evaluate('button => button.click()')
        page.wait_for_timeout(100)
        assert len(pending_comparison) == 1, 'Duplicate comparison submit'
        pending_comparison[0].continue_()
        page.unroute(comparison_url, hold_comparison)
        settle()
        expect(page.locator('#comparison-result > .mode-badge')).to_contain_text('Сравнение конкурентов')
        expect(page.locator('#comparison-result')).not_to_contain_text('Готово к сравнению')
        expect(page.locator('#comparison-result table')).to_contain_text('Сила призыва к действию')
        expect(page.locator('#comparison-result')).to_contain_text('Ограничения сравнения')
        expect(page.locator('#comparison-result')).to_contain_text(PAYLOAD)
        assert page.locator('#comparison-result tbody tr').count() == 2

        phase = 'expected comparison error'
        # Missing aggregate error envelope: expected HTTP error scope only.
        comparison_url = origin + '/api/v2/comparisons'
        def missing_aggregate(route):
            route.fulfill(status=400, content_type='application/json', body=json.dumps({
                'error': {'code': 'ANALYSIS_DATA_NOT_READY', 'message': 'No usable analyses', 'details': None}}))
        expected_http_errors.add(('POST', comparison_url, 400))
        page.route(comparison_url, missing_aggregate)
        page.locator('#compare-submit').click()
        expect(page.locator('#compare-error')).to_contain_text('сначала нужен сводный анализ')
        expect(page.locator('#comparison-result')).to_contain_text('Сравнение не завершено')
        expect(page.locator('#compare-choices input:checked')).to_have_count(2)
        expect(page.locator('#compare-error')).not_to_contain_text('анализ текущего snapshot')
        expect(page.locator('#compare-submit')).to_be_enabled()
        assert page.locator('#compare-dialog').is_visible()
        settle()
        expected_http_errors.remove(('POST', comparison_url, 400))
        page.unroute(comparison_url, missing_aggregate)
        page.locator('#close-compare').click()

        phase = 'expected form error'
        # Error message is inert plain text, dialog remains open and usable.
        create_url = origin + '/api/v2/competitors'
        expected_http_errors.add(('POST', create_url, 400))
        def form_error(route):
            route.fulfill(status=400, content_type='application/json', body=json.dumps({
                'error': {'code': 'CONTROLLED_ERROR', 'message': PAYLOAD, 'details': None}}))
        page.route(create_url, form_error)
        page.locator('#new-competitor').click()
        page.get_by_label('Название', exact=True).fill('Error fixture')
        page.locator('#form-submit').click()
        expect(page.locator('#form-error')).to_have_text(PAYLOAD)
        expect(page.locator('#form-submit')).to_be_enabled()
        assert page.locator('#editor-dialog').is_visible()
        settle()
        expected_http_errors.remove(('POST', create_url, 400))
        page.locator('#form-cancel').click()
        page.unroute(create_url, form_error)

        phase = 'races'
        # Artificially delayed A, fast B: release A only after B is active.
        def race_selection(url, first, second, first_action, second_action, active_id, expected_id, assert_view):
            held = []
            def hold(route):
                held.append((route, route.fetch()))
            page.route(url, hold)
            first_action()
            page.wait_for_function("async () => (await import('/static/js/state.js')).state.loading.size > 0")
            second_action()
            page.wait_for_function(f"async () => (await import('/static/js/state.js')).state.{active_id} === {json.dumps(expected_id)}")
            # Wait for B's detail, without waiting for the intentionally held A.
            assert_view()
            assert held, (first, second)
            for route, response in held:
                route.fulfill(response=response)
            page.unroute(url, hold)
            settle()
            assert page.evaluate(f"async () => (await import('/static/js/state.js')).state.{active_id}") == expected_id
            assert_view()

        race_selection(f'{origin}/api/v2/competitors/{alpha}', alpha, beta,
                       lambda: page.locator('#competitor-list .competitor-card').nth(0).click(),
                       lambda: page.locator('#competitor-list .competitor-card').nth(1).click(),
                       'activeCompetitorId', beta,
                       lambda: expect(page.locator('#competitor-name')).to_have_text('Beta'))
        page.locator('#competitor-list .competitor-card').nth(0).click()
        settle()
        race_selection(f'{origin}/api/v2/sources/{text_id}', text_id, url_id,
                       lambda: page.locator('#source-list .source-card').nth(0).click(),
                       lambda: page.locator('#source-list .source-card').last.click(),
                       'activeSourceId', url_id,
                       lambda: expect(page.locator('#source-preview')).to_contain_text('Страница 2'))

        phase = 'current snapshot empty'
        # Current snapshot with no analysis must not use historical analysis.
        source_url = f'{origin}/api/v2/sources/{url_id}'
        def no_analysis(route):
            response = route.fetch()
            detail = response.json()
            current_id = detail['snapshots'][-1]['id']
            detail['analyses'] = [a for a in detail['analyses'] if a['snapshot_id'] != current_id]
            route.fulfill(response=response, json=detail)
        page.route(source_url, no_analysis)
        page.locator('#source-list .source-card').last.click()
        settle()
        expect(page.get_by_text('Для текущего snapshot пока нет анализа', exact=True)).to_be_visible()
        assert page.locator('.summary').count() == 0
        page.unroute(source_url, no_analysis)
        page.locator('#source-list .source-card').last.click()
        settle()

        # A late mutation must not select its source after the user chooses another.
        phase = 'mutation race'
        mutation_url = f'{origin}/api/v2/sources/{url_id}/reanalyze'
        pending_mutations = []
        def hold_mutation(route):
            pending_mutations.append((route, route.fetch()))
        page.route(mutation_url, hold_mutation)
        page.get_by_role('button', name='Повторить анализ', exact=True).click()
        expect(page.get_by_role('button', name='Повторить анализ', exact=True)).to_be_disabled()
        deadline = monotonic() + 5
        while not pending_mutations and monotonic() < deadline:
            # Pump Playwright until the server response is held, before switching.
            page.wait_for_timeout(20)
        assert pending_mutations, 'Mutation response was not intercepted'
        page.locator('#source-list .source-card').nth(0).click()
        expect(page.locator('#source-preview h3')).to_have_text(PAYLOAD)
        for route, response in pending_mutations:
            route.fulfill(response=response)
        page.unroute(mutation_url, hold_mutation)
        settle()
        assert page.evaluate("async () => (await import('/static/js/state.js')).state.activeSourceId") == text_id
        expect(page.locator('#analysis-content h2')).to_have_text(PAYLOAD)
        page.locator('#source-list .source-card').last.click()
        settle()

        # Malicious values never produce executable nodes or arbitrary clickable schemes.
        assert page.locator('[onerror], [onload], svg, script:not([type=module])').count() == 0
        assert page.locator('a[href^="javascript:"], a[href^="data:"]').count() == 0
        assert page.evaluate("async () => (await import('/static/js/dom.js')).safeUrl('javascript:alert(1)')") is None
        assert page.evaluate("async () => (await import('/static/js/dom.js')).safeUrl('data:text/html,test')") is None
        for link in page.locator('a[target="_blank"]').all():
            assert link.get_attribute('rel') == 'noopener noreferrer'

        phase = 'responsive'
        # Layout, reachable actions, dialogs at all acceptance viewports.
        for label, width, height in [('desktop', 1440, 900), ('tablet', 1024, 768), ('mobile', 390, 844)]:
            page.set_viewport_size({'width': width, 'height': height})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), label
            for selector in ['#new-competitor', '#add-source', '#aggregate', '#open-compare']:
                page.locator(selector).scroll_into_view_if_needed()
                expect(page.locator(selector)).to_be_visible()
            if label == 'desktop':
                boxes = [page.locator('#' + pane).bounding_box() for pane in ['competitors-pane', 'sources-pane', 'analysis-pane']]
                assert boxes[0]['width'] == 260 and boxes[1]['width'] == 370
                assert boxes[0]['x'] < boxes[1]['x'] < boxes[2]['x']
            if label == 'mobile':
                page.get_by_role('link', name='Анализ', exact=True).click()
                expect(page.locator('#analysis-pane h2').first).to_be_visible()
                # Native score disclosures remain readable and keyboard operable.
                page.locator('.scorecard details summary').first.focus()
                page.keyboard.press('Enter')
                expect(page.locator('.scorecard details p').first).to_be_visible()
                page.keyboard.press('Enter')
                expect(page.locator('.scorecard details p').first).not_to_be_visible()
            page.locator('#open-compare').click()
            box = page.locator('#compare-dialog').bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= width + 1 and box['height'] <= height, (label, box)
            page.locator('#close-compare').click()
            page.locator('#new-competitor').click()
            box = page.locator('#editor-dialog').bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= width + 1 and box['height'] <= height, (label, box)
            page.locator('#form-cancel').click()
            page.locator('#analysis-pane').scroll_into_view_if_needed()
            page.screenshot(path=str(artifacts / f'{label}.png'), full_page=True)

        phase = 'deletions'
        page.set_viewport_size({'width': 1440, 'height': 900})
        page.on('dialog', lambda dialog: dialog.accept())
        with page.expect_response(lambda response: response.request.method == 'DELETE' and '/sources/' in response.url):
            page.get_by_role('button', name='Удалить источник', exact=True).click()
        settle()
        assert page.evaluate("async () => (await import('/static/js/state.js')).state.activeSourceId") != url_id
        assert page.get_by_role('button', name='Обновить страницу', exact=True).count() == 0
        with page.expect_response(lambda response: response.request.method == 'DELETE' and '/competitors/' in response.url):
            page.locator('#delete-competitor').click()
        settle()
        expect(page.locator('#competitor-name')).to_have_text('Beta')
        with page.expect_response(lambda response: response.request.method == 'DELETE' and '/competitors/' in response.url):
            page.locator('#delete-competitor').click()
        settle()
        expect(page.get_by_text('Пока нет конкурентов', exact=True)).to_be_visible()
        assert page.locator('#source-preview img').count() == 0

        phase = 'five selection'
        # Five is allowed, six is blocked. Explicit UI fixtures, no hidden aggregates.
        for index in range(6):
            response = context.request.post(origin + '/api/v2/competitors', data={'name': f'Choice {index}'})
            assert response.status == 201
        page.locator('#retry-load').click()
        settle()
        page.locator('#open-compare').click()
        for index in range(5):
            page.locator('#compare-choices input').nth(index).check()
        expect(page.locator('#compare-choices input').nth(5)).to_be_disabled()
        expect(page.locator('#compare-submit')).to_be_enabled()
        expect(page.locator('#compare-count')).to_have_text('Выбрано: 5 из 5')
        expect(page.locator('#comparison-result')).to_contain_text('Готово к сравнению')
        page.locator('#close-compare').click()

        assert any(method == 'POST' and '/sources/file' in url and 'multipart/form-data; boundary=' in ctype for method, url, ctype in requests)
        required = ['/sources/text', '/sources/file', '/sources/url', '/reanalyze', '/refresh', '/aggregate-analysis', '/comparisons']
        for path in required:
            assert any(method == 'POST' and path in url for method, url, _ in requests), path
        assert any(method == 'PATCH' and '/competitors/' in url for method, url, _ in requests)
        assert any(method == 'DELETE' and '/sources/' in url for method, url, _ in requests)
        assert any(method == 'DELETE' and '/competitors/' in url for method, url, _ in requests)
        assert not any(any(path in url for path in ['/analyze_text', '/analyze_image', '/parse_demo', '/history']) for _, url, _ in requests)
        assert not external, external
        assert not errors, errors
        unexpected_console_errors = [entry for entry in console_errors if not expected_http_diagnostic(entry)]
        assert not unexpected_console_errors, unexpected_console_errors
        assert not failed, failed
        assert not bad_assets, bad_assets
        print(f'Chromium: {browser.version}; server: {origin}; temporary SQLite/uploads/screenshots')
        print(f'console errors=0 unexpected; all {len(console_errors)} console error events audited; '
              f'expected HTTP 400 diagnostics={len(console_errors) - len(unexpected_console_errors)}')
        print('page errors=0; unexpected external requests=0; failed requests/static assets=0')
        print('desktop=1440x900 PASS; tablet=1024x768 PASS; mobile=390x844 PASS; dialogs/overflow/actions PASS')
        print('CRUD/text/image/PDF/URL/reanalyze/refresh/aggregate/existing aggregate/history/compare PASS')
        print('aggregate-not-ready/comparison-not-ready/console audit/loading/duplicate submit/errors/competitor race/source race/mutation race/current snapshot/XSS/empty states/2-5 selection PASS')
        print(f'Screenshots: {artifacts}')
        context.close()
        browser.close()


def main():
    before = filesystem()
    runtime_root = ROOT / '.pytest-runtime'
    runtime_root.mkdir(exist_ok=True)
    try:
        with TemporaryDirectory(prefix='stage8-', dir=runtime_root) as directory:
            runtime = Path(directory)
            configure(runtime)
            png, captures = fake_boundaries()
            from backend.main import app
            import uvicorn
            logging.getLogger('competitor_monitor.api').setLevel(logging.ERROR)
            # Bind before starting the server to eliminate ephemeral port races.
            listener = socket.socket()
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error', access_log=False))
            thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
            thread.start()
            deadline = monotonic() + 15
            try:
                while not server.started and thread.is_alive() and monotonic() < deadline:
                    sleep(.05)
                assert server.started, 'Local server did not start'
                run_browser(f'http://127.0.0.1:{port}', png, captures)
            finally:
                server.should_exit = True
                thread.join(timeout=15)
                listener.close()
                assert not thread.is_alive(), 'Server did not stop'
    finally:
        assert filesystem() == before, 'Production filesystem changed'
        if not any(runtime_root.iterdir()):
            runtime_root.rmdir()
    print('PRODUCTION_FILESYSTEM_UNCHANGED; STAGE_8_BROWSER_SMOKE_PASS')


if __name__ == '__main__':
    main()
