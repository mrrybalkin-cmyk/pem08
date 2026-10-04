"""Read-only Stage 11 capture of real local reports, never runs AI or writes data.

python -m tests.product_visual_acceptance baseline|final
Artifacts are ignored; comparison is injected from a persisted SQLite result
because the product has no comparison-history GET endpoint.
"""
import json
import logging
import os
import socket
import sqlite3
import subprocess
import sys
from contextlib import asynccontextmanager
from threading import Thread
from time import monotonic, sleep

from tests.browser_ui_smoke import ROOT, configure, filesystem


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else 'final'
    assert phase in {'baseline', 'final'}
    before = filesystem()
    database = ROOT / 'data/app.db'
    assert database.is_file(), 'Real local database required'
    configure(ROOT / '.pytest-temp/product-review')
    os.environ['DATABASE_URL'] = f'sqlite:///file:{database.as_posix()}?mode=ro&uri=true'
    os.environ['UPLOAD_DIR'] = str(ROOT / 'data/uploads')
    os.environ['SCREENSHOT_DIR'] = str(ROOT / 'data/screenshots')
    artifacts = ROOT / '.pytest-temp/product-review' / phase
    artifacts.mkdir(parents=True, exist_ok=True)
    from backend.main import app
    from backend.database import engine
    logging.getLogger('competitor_monitor.api').setLevel(logging.ERROR)
    from starlette.responses import Response
    import uvicorn
    from playwright.sync_api import sync_playwright, expect

    @asynccontextmanager
    async def lifespan(_app):
        yield  # Skip startup migrations/storage/AI initialization.
        engine.dispose()

    app.router.lifespan_context = lifespan

    @app.middleware('http')
    async def read_only(request, call_next):
        if request.method not in {'GET', 'HEAD'}:
            return Response(status_code=405)
        return await call_next(request)

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 15
        while not server.started and thread.is_alive() and monotonic() < deadline:
            sleep(.05)
        assert server.started
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900})
            page = context.new_page()
            issues = {key: [] for key in ('console', 'page', 'failed', 'http', 'external', 'mutation')}
            page.on('console', lambda msg: issues['console'].append(msg.text) if msg.type == 'error' else None)
            page.on('pageerror', lambda error: issues['page'].append(str(error)))
            page.on('requestfailed', lambda request: issues['failed'].append(request.url))
            page.on('response', lambda response: issues['http'].append(response.url) if response.status >= 400 else None)

            def guard(route):
                request = route.request
                key = 'external' if not request.url.startswith(origin + '/') else 'mutation' if request.method not in {'GET', 'HEAD'} else None
                if key:
                    issues[key].append(request.url)
                    route.abort()
                else:
                    if phase == 'baseline' and (request.url == origin + '/' or '/static/' in request.url):
                        asset = 'index.html' if request.url == origin + '/' else request.url.split('/static/', 1)[1]
                        content = subprocess.check_output(['git', '-c', f'safe.directory={ROOT.as_posix()}', 'show', f'HEAD:frontend/{asset}'], cwd=ROOT)
                        route.fulfill(body=content, content_type='text/html' if asset.endswith('.html') else 'text/css' if asset.endswith('.css') else 'text/javascript')
                    else:
                        route.continue_()

            context.route('**/*', guard)
            shots, viewports = [], []

            def settle():
                page.wait_for_load_state('networkidle')
                page.wait_for_function("async () => !(await import('/static/js/state.js')).state.loading.size")

            def capture(name, full=True):
                settle()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
                page.screenshot(path=str(artifacts / f'{name}.png'), full_page=full)
                shots.append(f'{name}.png')

            page.goto(origin)
            capture('desktop-first-impression')
            for name in ('Anthropic', 'OpenAI'):
                page.locator('.competitor-card').filter(has=page.locator('strong', has_text=name)).click()
                settle()
                if name == 'Anthropic':
                    expect(page.locator('.summary')).to_be_visible()
                    capture('anthropic-source')
                    if phase == 'final':
                        expect(page.locator('.mode-badge').first).to_have_text('Анализ отдельного источника')
                page.locator('#latest-aggregate').click()
                expect(page.locator('.summary')).to_be_visible()
                capture(name.lower() + '-aggregate')
                if phase == 'final':
                    expect(page.locator('.mode-badge').first).to_have_text('Сводный анализ конкурента')
                    original = page.evaluate("async () => (await import('/static/js/state.js')).state.activeAnalysis.result_json.executive_summary")
                    displayed = ' '.join(page.locator('#analysis-content .summary > p').all_text_contents())
                    assert displayed.split() == original.split(), 'Summary words changed'
            page.locator('#open-compare').click()
            capture('comparison-zero')
            for name in ('Anthropic', 'OpenAI'):
                page.locator('.compare-choice').filter(has_text=name).locator('input').check()
            expect(page.locator('#compare-submit')).to_be_enabled()
            capture('comparison-ready')
            if phase == 'final':
                expect(page.locator('#comparison-result')).to_contain_text('Готово к сравнению')
            with sqlite3.connect(f'file:{database.as_posix()}?mode=ro', uri=True) as db:
                row = db.execute('SELECT result_json FROM comparisons ORDER BY created_at DESC, id DESC LIMIT 1').fetchone()
            if row:
                page.evaluate("async result => { const {state} = await import('/static/js/state.js'); state.comparisonResult = result; (await import('/static/js/compare.js')).renderCompare({compareSelect() {}}); }", json.loads(row[0]))
                capture('comparison-existing-result')
            page.keyboard.press('Escape')
            for name, width, height in [('desktop', 1440, 900), ('tablet', 1024, 768), ('mobile', 390, 844)]:
                page.set_viewport_size({'width': width, 'height': height})
                page.locator('#latest-aggregate').click()
                if phase == 'final' and name == 'mobile':
                    expect(page.locator('#analysis-content .analysis-body > details[open]')).to_have_count(0)
                for selector in ('#new-competitor', '#add-source', '#aggregate', '#open-compare'):
                    page.locator(selector).scroll_into_view_if_needed()
                    expect(page.locator(selector)).to_be_visible()
                page.locator('#analysis-pane').scroll_into_view_if_needed()
                capture(name + '-aggregate')
                if name == 'mobile':
                    page.locator('.report-context' if phase == 'final' else '#analysis-content .summary').scroll_into_view_if_needed()
                else:
                    page.locator('#analysis-pane').evaluate('pane => pane.scrollTop = 0')
                capture(name + '-report-viewport', full=False)
                for trigger, dialog in [('#open-compare', '#compare-dialog'), ('#new-competitor', '#editor-dialog')]:
                    page.locator(trigger).click()
                    box = page.locator(dialog).bounding_box()
                    assert box['x'] >= 0 and box['x'] + box['width'] <= width + 1 and box['height'] <= height
                    capture(name + dialog[1:], full=False)
                    page.keyboard.press('Escape')
                    expect(page.locator(dialog)).not_to_be_visible()
                    expect(page.locator(trigger)).to_be_focused()
                viewports.append({'name': name, 'size': [width, height], 'overflow': False, 'actions': 'PASS', 'dialogs': 'PASS', 'keyboard': 'PASS'})
            assert not any(issues.values()), issues
            assert filesystem() == before
            report = {'screenshots': shots, 'viewports': viewports, 'errors': {key: len(value) for key, value in issues.items()},
                      'browser': browser.version, 'filesystem_unchanged': True, 'persisted_comparison': bool(row),
                      'filesystem_hashes': before,
                      'comparison_method': 'existing SQLite result rendered locally; no API mutation'}
            (artifacts / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report, ensure_ascii=False))
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
        assert not thread.is_alive()
        assert filesystem() == before, 'Production filesystem changed'
    print('PRODUCT_READONLY_VISUAL_ACCEPTANCE_PASS')


if __name__ == '__main__':
    main()
