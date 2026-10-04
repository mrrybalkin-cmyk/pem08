"""Stage 8 zero-build entry and safe rendering contract; legacy kept offline."""
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
        assert 'type="module" src="/static/js/app.js"' in html
        assert '/static/app.js' not in html
        assert 'data-tab="text"' not in html
        assert "fonts.googleapis" not in html
        assert client.get('/static/workspace.css').status_code == 200
        for module in (ROOT / 'frontend/js').glob('*.js'):
            result = client.get(f'/static/js/{module.name}')
            assert result.status_code == 200
            assert 'javascript' in result.headers['content-type']


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


def test_legacy_assets_retained_unmodified_primary_entry_separate():
    assert (ROOT / 'frontend/legacy-index.html').exists()
    assert (ROOT / 'frontend/app.js').exists()
    assert (ROOT / 'frontend/styles.css').exists()


def test_responsive_layout_contract():
    css = (ROOT / 'frontend/workspace.css').read_text()
    assert '260px 370px minmax(0, 1fr)' in css
    assert 'max-width: 1199px' in css
    assert 'max-width: 767px' in css
    assert ':focus-visible' in css
