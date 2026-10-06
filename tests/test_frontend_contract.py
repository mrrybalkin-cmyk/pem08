"""Stage 8 zero-build entry and safe rendering contract; legacy removed."""
from pathlib import Path
import re

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def test_root_is_module_workspace_and_assets_load(app):
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        html = response.text
        assert 'lang="ru"' in html
        for pane in ("competitors-pane", "sources-pane", "analysis-pane"):
            assert f'id="{pane}"' in html
        assert 'type="module" src="/static/js/app.js?v=source-lifecycle-20261007"' in html
        assert response.headers['cache-control'] == 'no-cache, max-age=0, must-revalidate'
        assert '/static/app.js' not in html
        assert 'data-tab="text"' not in html
        assert "fonts.googleapis" not in html
        assert client.get('/static/workspace.css').status_code == 200
        for module in (ROOT / 'frontend/js').glob('*.js'):
            result = client.get(f'/static/js/{module.name}')
            assert result.status_code == 200
            assert 'javascript' in result.headers['content-type']
            assert result.headers['cache-control'] == 'no-cache, max-age=0, must-revalidate'


def test_module_cache_revalidation_keeps_policy_on_304(app):
    with TestClient(app) as client:
        response = client.get('/static/js/app.js')
        cached = client.get('/static/js/app.js', headers={'If-None-Match': response.headers['etag']})
        assert cached.status_code == 304
        assert cached.headers['cache-control'] == 'no-cache, max-age=0, must-revalidate'
        assert 'cache-control' not in client.get('/api/v2/competitors').headers


def test_v2_modules_have_single_fetch_boundary_and_no_html_sinks():
    modules = list((ROOT / 'frontend/js').glob('*.js'))
    assert len(modules) >= 4
    for module in modules:
        text = module.read_text(encoding='utf-8')
        assert not re.search(r'innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\s*\(|new\s+Function\s*\(', text), module
        assert not re.search(r'/analyze_text|/analyze_image|/parse_demo|["\']/history', text), module
        if module.name != 'api.js':
            assert not re.search(r'\bfetch\s*\(', text), module
    assert 'textContent' in (ROOT / 'frontend/js/dom.js').read_text()
    assert "['http:', 'https:']" in (ROOT / 'frontend/js/dom.js').read_text()
    assert "noopener noreferrer" in (ROOT / 'frontend/js/dom.js').read_text()


def test_legacy_assets_removed_after_cutover():
    assert not (ROOT / 'frontend/legacy-index.html').exists()
    assert not (ROOT / 'frontend/app.js').exists()
    assert not (ROOT / 'frontend/styles.css').exists()


def test_responsive_layout_contract():
    css = (ROOT / 'frontend/workspace.css').read_text()
    assert '260px 370px minmax(0, 1fr)' in css
    assert 'max-width: 1199px' in css
    assert 'max-width: 767px' in css
    assert ':focus-visible' in css
