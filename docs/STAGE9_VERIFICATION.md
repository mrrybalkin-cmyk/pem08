# Stage 9 hardening / cutover inventory

Baseline verified before edits: `develop-v2`, `11ed34fe3e7098d229b54a119ce1a23b6ce09a0b`, clean worktree.
No branch/history/index changes are authorized.

## Reference inventory before deletion

Inventory used `git grep`, Python AST imports and PowerShell reads, not `rg`.

| Candidate | References before | Action taken | Reason |
|---|---|---|---|
| v1 endpoints + duplicate `/health` | main.py, old frontend, desktop API client, README/docs.md, compatibility tests | Removed callers + endpoints | v2 routers/workspace own the workflow |
| services/openai_service.py | main.py, lifespan.py, test_ai_service_mocked.py, test_health.py, test_lifespan.py; old docs | Removed callers/tests + file | v2 ai_service uses AsyncOpenAI and strict schemas |
| services/parser_service.py | main.py, lifespan.py, test_lifespan.py; old docs | Removed callers/tests + file | v2 BrowserService owns async Playwright |
| services/history_service.py | main.py, tests/test_history.py; old docs | Removed callers/tests + file | SQLite is the runtime state store |
| models/schemas.py | main.py, legacy AI/history services; old docs | Removed | v2 schemas are models/api.py and models/analysis.py |
| frontend/app.js, styles.css, legacy-index.html | legacy-index.html, frontend-retention test; old docs | Removed; cutover assertion replaces retention test | Root already uses workspace.css and js/ ES modules |
| desktop/main.py, api_client.py, styles.py | desktop-local imports/build/README; old docs | Removed all desktop callers/files | Only calls v1 endpoints; no v2 imports |
| desktop/build.py, CompetitorMonitor.spec, requirements.txt, README.md | desktop-only build/instructions | Removed | No active core caller; duplicate v1 application |
| history.json | legacy history configuration/service and old docs | Removed | Versioned baseline retains old data; never imported into v2 |
| tests/test_history.py | legacy HistoryService only | Removed | No v2 semantics tested |

All backend/api, services, models, repositories, security, lifecycle/database/health,
frontend/test imports and README/config/requirements/startup were inspected.
Current docs.md is rewritten for v2; historical spec/migration audit stay unchanged.

## Final result and observed failures

STAGE 9 RESULT: PASS. Baseline HEAD and branch unchanged; index empty; no staging/commit/push/history mutation.
Only Stage 9 hardening/cutover. No product feature, UI redesign, AI schema, score definition, DB schema, authentication, Docker or build-step change.

Inventory correction: the relative wildcard `from .schemas import *` in models/__init__.py was missed by the first inventory. First pytest collection exposed ModuleNotFoundError after schema removal. It was replaced with an explicit-import package docstring; fresh import and full regression then passed. This was an actual failed attempt, not a successful pre-deletion zero-reference proof for that one caller.

Other early failures and corrections:

- Editing script initially used the Windows default encoding for config.py and failed with UnicodeDecodeError. All later reads/writes used explicit UTF-8; the original file-upload default label was restored exactly from HEAD after two regression failures exposed its changed encoding.
- Sandbox pytest cleanup returned WinError 5 for .pytest-temp (19 passed, 26 fixture errors). Same isolated tests ran outside sandbox with approval; 45 passed. No security/config bypass was introduced.
- First focused cutover run: 246 passed / 4 failed: one inaccurate test code URL_BLOCKED (actual preserved code URL_BLOCKED_PRIVATE_NETWORK), one old raises(OSError) expectation now deliberately HTTP 500, and two default-label encoding failures. Next run: 249 passed / 1 failed because the first replacement missed the nested assertion; exact patch fixed it. Subsequent focused runs: 250 passed, then 251 passed after adding the outer-boundary regression.
- An intermediate git diff --check found an extra EOF blank line after removing legacy tests; removed it. Final check clean.

## Legacy inventory after cutover

Active runtime references to removed modules/files: ZERO. Models package relative import corrected as noted above. Main/lifespan/config no legacy callers; v1 tests removed or replaced with v2 envelope/cutover assertions. All package modules import successfully. Historical spec/migration plan retained unchanged; negative tests and this audit may mention old names without executing them.

## Deleted files and reason (exact list)

| File | Reason |
|---|---|
| `backend/models/schemas.py` | V1 request/response schemas; v2 uses api.py / analysis.py. Removed the remaining models/__init__.py re-export after the first collection failure. |
| `backend/services/history_service.py` | JSON history service; no active v2 caller, SQLite only. |
| `backend/services/openai_service.py` | V1 sync provider runtime and regex JSON parser; replaced earlier by structured AsyncOpenAI service. |
| `backend/services/parser_service.py` | V1 Selenium parser; async Playwright is the verified v2 implementation. |
| `desktop/CompetitorMonitor.spec` | V1-only desktop runtime/build/documentation; internal callers only, no v2 core import. Core/Web-only cutover. |
| `desktop/README.md` | V1-only desktop runtime/build/documentation; internal callers only, no v2 core import. Core/Web-only cutover. |
| `desktop/api_client.py` | V1-only desktop runtime/build/documentation; internal callers only, no v2 core import. Core/Web-only cutover. |
| `desktop/build.py` | V1-only desktop runtime/build/documentation; internal callers only, no v2 core import. Core/Web-only cutover. |
| `desktop/main.py` | V1-only desktop runtime/build/documentation; internal callers only, no v2 core import. Core/Web-only cutover. |
| `desktop/requirements.txt` | V1-only desktop runtime/build/documentation; internal callers only, no v2 core import. Core/Web-only cutover. |
| `desktop/styles.py` | V1-only desktop runtime/build/documentation; internal callers only, no v2 core import. Core/Web-only cutover. |
| `frontend/app.js` | Unsafe v1 four-tab code; no active workspace caller. |
| `frontend/legacy-index.html` | V1 entry; root serves the Stage 8 workspace. |
| `frontend/styles.css` | V1 styling referenced only by legacy-index.html. |
| `history.json` | Old versioned v1 records, not imported into v2; recoverable from baseline Git history. |
| `tests/test_history.py` | Tests only removed V1 HistoryService; SQLite history/analysis regressions retained. |

## Changed/new files (exact list; deletions listed above)

- `.env.example`
- `README.md`
- `backend/api/analyses.py`
- `backend/api/comparisons.py`
- `backend/api/competitors.py`
- `backend/api/health.py`
- `backend/api/sources.py`
- `backend/config.py`
- `backend/lifespan.py`
- `backend/main.py`
- `backend/models/__init__.py`
- `docs.md`
- `requirements-dev.txt`
- `requirements.txt`
- `run.py`
- `tests/browser_ui_smoke.py`
- `tests/conftest.py`
- `tests/test_ai_service_mocked.py`
- `tests/test_competitors.py`
- `tests/test_config.py`
- `tests/test_frontend_contract.py`
- `tests/test_health.py`
- `tests/test_lifespan.py`
- `tests/test_sources.py`
- `tests/test_url_sources.py`
- `backend/api/errors.py`
- `docs/STAGE9_VERIFICATION.md`
- `tests/stage9_acceptance.py`
- `tests/startup_smoke.py`
- `tests/test_stage9_cleanup.py`
- `tests/test_stage9_hardening.py`

## Error contract

One `backend/api/errors.py` factory and `V2Route` shared by ALL v2 routers. Global framework HTTP/validation handlers and outer request middleware reuse the same factory; unexpected root/file response failures do not escape as raw exceptions. Cancellation remains cancellation, not an artificial successful response.
HTTP statuses tested: 400, 404, 413, 422, 500, 502, 504. CRUD unknown/malformed/write failures, missing analyses/sources, invalid/oversized uploads, readiness errors, browser/provider timeout and provider failure covered. OpenAPI documents the shared envelope. HTTP 204 success preserved; no 200/success:false.

Representative response: HTTP 500 `{"error":{"code":"INTERNAL_ERROR","message":"Operation failed","details":null}}`.
Framework CORS preflight rejection is standard middleware HTTP 400, not an API JSON response; documented in README.

## Logging / CORS

Request logging: method, matched route template (no user-controlled path value/query), status, duration_ms and controlled code. Unexpected failure: exception type and traceback frames (filename/line/function), no exception value/chain/locals/source text. These paths exist ONLY in server diagnostics, never client responses. Uvicorn raw access log disabled; httpx/httpx2/httpcore/openai loggers limited. No authorization, keys, env contents, user documents, upload/base64 or AI payload logs. Sentinel regression verifies both response and log safety.

CORS from settings, default localhost/127.0.0.1 origins only. Explicit HTTP(S) origins; wildcards/credentials/paths/query/fragment rejected in config. Empty config disables cross-origin access. allow_credentials=False; methods GET/POST/PATCH/DELETE/OPTIONS; Content-Type header. Allowed/hostile actual requests, allowed/denied preflights, invalid method/header tested. Startup smoke additionally proves a custom ephemeral configured origin works and hostile origin receives no allow-origin.

## Cleanup / semantic preservation

New mixed-type regression (4 parameter combinations) covers individual source vs competitor deletion, success vs DB commit rollback, text/image/PDF/URL, two URL snapshots with primary AND secondary screenshots, exact byte restoration and unrelated UUID artifact protection in both storage directories. Existing file/PDF/URL tests cover invalid ingestion/capture, file/write/DB failures, retryable AI failure after source commit, URL refresh failures and cancellation. No service/data-model changes needed.

Verified: reanalyze adds analysis without browser capture; refresh adds snapshot; aggregates are analysis_type=aggregate / snapshot_id=null; historical persisted comparison survives competitor deletion (2 and 5 participants). Safe preview resolver remains DB-bound, no arbitrary filesystem URL. PDF preview remains metadata/extracted text/page metadata; image/URL use safe image/PNG artifact route.

## Dependency baseline

Removed requirements: beautifulsoup4, lxml, aiofiles (legacy-only use), selenium and webdriver-manager. Removed desktop requirements including PyQt6/desktop packaging; no core desktop import. Did not uninstall anything or mutate the external venv.
Retained/pinned verified direct runtime versions: FastAPI 0.142.2, Uvicorn 0.54.0, OpenAI 3.23.0, httpx 0.28.1, python-multipart 0.0.32, Pydantic 2.13.5, pydantic-settings 2.15.0, python-dotenv 1.2.4, Pillow 12.3.0, SQLAlchemy 2.1.2, PyMuPDF 1.28.2, Playwright 1.63.0. Dev: pytest 9.1.1, pytest-asyncio 1.4.0. dotenv is required dynamically by pydantic-settings; OpenAI's transitive httpx2 dependency is not removed based on static search. pip check: No broken requirements found.

## Documentation / startup

README and docs.md rewritten for actual v2 workflow/API, persistence, artifact previews, offline tests, limits and security boundaries. .env.example contains supported v2 config only; host/port aliases are retained intentionally in config and documented, without v1 runtime. Spec and migration history unchanged.
Only application startup command: `python run.py`. Installation and Playwright Chromium provisioning documented. Development enables reload; test/production disables it. Core startup does not create AI/browser clients eagerly.
Startup smoke executed the real command in a subprocess on 127.0.0.1, temporary DB/storage, no AI key. Root/health/docs/redoc/OpenAPI/CSS/all 7 ES modules returned 200; honest health database=ready, browser=not_initialized, ai_configured=false. CTRL_BREAK graceful shutdown confirmed by Application stopped + Application shutdown complete; temporary runtime removed.

## Final route inventory

| Methods | Path |
|---|---|
| GET | /api/v2/health |
| GET, POST | /api/v2/competitors |
| GET, PATCH, DELETE | /api/v2/competitors/{competitor_id} |
| POST | /api/v2/competitors/{competitor_id}/sources/text |
| POST | /api/v2/competitors/{competitor_id}/sources/file |
| POST | /api/v2/competitors/{competitor_id}/sources/url |
| GET, DELETE | /api/v2/sources/{source_id} |
| POST | /api/v2/sources/{source_id}/reanalyze |
| POST | /api/v2/sources/{source_id}/refresh |
| GET | /api/v2/sources/{source_id}/snapshots/{snapshot_id}/artifact |
| GET | /api/v2/competitors/{competitor_id}/analyses |
| GET | /api/v2/analyses/{analysis_id} |
| POST | /api/v2/competitors/{competitor_id}/aggregate-analysis |
| POST | /api/v2/comparisons |

Root/static/docs/redoc/OpenAPI retained. Legacy analyzer/parse/history and old /health routes absent; tested 404. No compatibility wrappers.

## Restart persistence (explicit acceptance)

`python -m tests.stage9_acceptance`: two FRESH application subprocesses using the SAME temporary SQLite/uploads/screenshots. Real FastAPI lifespan, SQLAlchemy, SQLite, ingestion/storage/routing. Only AI and browser provider boundaries fake; external network blocked. Test runtime path guarded under .pytest-runtime; no production state.

Before: 3 competitors, 6 sources (3 text + image + PDF + URL), 7 snapshots, 11 analyses (8 source + 3 aggregate), 1 comparison of 2 competitors. URL refresh creates distinct snapshot; URL reanalyze creates analysis without capture. After stopping process and starting a fresh process: all IDs/counts/snapshots/analyses/aggregate fields and artifact bytes match; comparison result/participants match through repository contract (no public comparison GET invented). Both lifespans cleanly shut down; temporary runtime cleaned. Explicit acceptance PASS.

## Browser verification

- Real local Chromium capture: title/meta/text/PNG, redirect and subrequest policy, unsafe redirect blocking, contexts cleanup PASS. Test-only loopback injection; production policy unchanged. No public website capture.
- Stage 8 browser UI smoke: Chromium 153.0.8010.12; local FastAPI temporary runtime.
- Desktop 1440×900, tablet 1024×768, mobile 390×844: PASS; dialogs/primary actions/readability/overflow checked.
- Unexpected console errors=0; RAW console error events=3, all audited exact expected HTTP 400 resource diagnostics. No JS errors were muted. Page errors=0, unexpected external requests=0, failed requests/assets=0.
- Competitor CRUD; text/image/PDF/URL sources; delete; preview; reanalyze; refresh; aggregate/existing aggregate/history; comparison and limitations: PASS.
- Visible score rationale + evidence, XSS inert payloads, loading/duplicate submit, safe errors, current snapshot, competitor/source/mutation races, empty states and 2–5 selection: PASS.
- UI was not redesigned; active frontend source unchanged. Browser smoke strengthened with a visible rationale assertion.

## Full verification evidence

| Check | Observed result |
|---|---|
| Focused Stage 9 + cleanup/errors/CORS/routes/storage/preview/aggregate/compare regression | 251 passed |
| Earlier health/config/lifespan/CRUD/frontend targeted check | 45 passed |
| Restart fresh-process acceptance | PASS |
| Real local Chromium capture | PASS |
| Stage 8 Chromium UI smoke | PASS |
| Full python -m pytest -q | 506 passed in 36.29s |
| Active ES modules node --check (7 files) | exit 0 |
| python -m compileall -q backend tests | exit 0 |
| Fresh import | IMPORT_OK, exit 0 |
| Real python run.py startup/shutdown | PASS |
| pip check | No broken requirements found |
| git diff --check + untracked text whitespace scan | PASS |
| Final runtime AST/reference scan | PASS; no forbidden executable legacy references |
| git ls-files .env | empty |
| git diff --cached --name-only | empty |

Production before/after: data/app.db ABSENT/ABSENT; uploads files 0/0; screenshots files 0/0. Acceptance/UI/startup smoke additionally compare hashes of every production artifact and DB; unchanged. Restart/startup temporary runtimes cleaned; ignored UI screenshots are test artifacts only.

## §21 Definition of Done matrix (all actual items)

| # | Actual specification item | Evidence | Result |
|---|---|---|---|
| 1 | приложение запускается одной командой `python run.py` | Actual subprocess tests.startup_smoke, documented run.py command | PASS |
| 2 | `/api/v2/health` = 200 | startup smoke + health regressions HTTP 200 honest state | PASS |
| 3 | UI открывается на localhost | Chromium local workspace smoke + actual run.py root HTTP 200 | PASS |
| 4 | можно создать минимум 3 конкурентов | Restart acceptance creates Alpha/Beta/Gamma through API | PASS |
| 5 | text source анализируется и сохраняется | Restart acceptance + test_sources pipeline/save/provider-boundary tests | PASS |
| 6 | image source анализируется и сохраняется | Restart acceptance + test_sources / AI multimodal tests + UI smoke | PASS |
| 7 | PDF source реально анализируется | Real PyMuPDF text/scanned/mixed page preparation and test_pdf_end_to_end_and_reanalyze; deterministic AI boundary; persisted analysis + UI | PASS |
| 8 | URL открывается Playwright и анализируется по text + screenshot | Real browser_local_smoke capture + URL API prepared text AND screenshot assertions + structured multimodal SDK tests | PASS |
| 9 | URL private network блокируется | URL policy tests + private URL API 400 + real Chromium unsafe redirect/subrequest checks | PASS |
| 10 | результат соответствует `CompetitorAnalysis` | Strict SDK/Pydantic wire tests and persisted results revalidated after restart | PASS |
| 11 | evidence отображается в UI | UI Доказательства tab rendered inert evidence payload | PASS |
| 12 | score rationale отображается в UI | UI visible .scorecard .score p rationale assertion + numeric score | PASS |
| 13 | reanalyze создаёт новую запись анализа | API source tests + restart URL reanalyze counts, no capture | PASS |
| 14 | URL refresh создаёт новый snapshot | URL tests + restart acceptance distinct new snapshot ID | PASS |
| 15 | aggregate-analysis работает | Aggregate tests + 3 persisted aggregate rows across restart + UI | PASS |
| 16 | compare 2–5 competitors работает | Comparison tests successful 2 AND 5, invalid cardinality/duplicates, UI and restart 2 | PASS |
| 17 | нет regex JSON parsing | Runtime AI uses chat.completions.parse; no re import/regex extraction; strict wire tests | PASS |
| 18 | нет `webdriver-manager` | No dependency/import/runtime module; static cutover regression | PASS |
| 19 | нет `history.json` как runtime storage | No active runtime string or storage service; SQLite persistence + removed file | PASS |
| 20 | нет raw user/AI data через `innerHTML` | Active frontend DOM/textContent scan + real malicious payload browser regression | PASS |
| 21 | PyQt6 не нужен для запуска core проекта | Desktop removed; dependency/import AST scan + actual core startup | PASS |
| 22 | `.env` не попадает в Git | git ls-files .env empty; .gitignore excludes .env / .env.* except example | PASS |
| 23 | `pytest -q` проходит | Full regression: 506 passed | PASS |
| 24 | README соответствует реальному функционалу | README reflects tested v2 routes/config/workflow/start command/limits; legacy manual replaced | PASS |

Verification boundary: no real external AI request or public website was made. User explicitly permits deterministic provider boundaries. Real PDF/image preparation, SQL/storage/routing/restart, SDK wire validation and local Chromium are exercised; live provider availability remains environment-dependent, not an unverified Stage 9 acceptance claim.

## Decisions where the spec is silent

- Logging uses route templates to preserve useful path context without recording user-controlled IDs/query. Unexpected traceback frames and type are retained; exception messages/locals omitted to prevent secret/document leakage.
- Dependency baseline pins tested direct versions without altering installed environment; transitive SDK dependencies remain resolver-owned.
- Restart proof uses two fresh processes, with comparison verified through the existing repository contract; no new comparison read endpoint added.
- Existing API_HOST/API_PORT aliases remain settings-only conveniences; no legacy runtime endpoint/service remains.
- PDF preview strategy and Stage 8 responsive breakpoints/modal/state/race handling are unchanged; Stage 9 adds verification rather than redesign.

## Applied guidance

Security-best-practices skill was read for the authorized hardening work:

- ~/.codex/skills/security-best-practices/SKILL.md
- ~/.codex/skills/security-best-practices/references/python-fastapi-web-server-security.md
- ~/.codex/skills/security-best-practices/references/javascript-general-web-frontend-security.md

User scope takes precedence: no new authentication, UI framework or removal of Swagger/OpenAPI. No skill-driven publication or messaging action.

## Remaining references: historical or negative-test only

The table below records exact locations from the final scan. Historical spec/plan are immutable design/audit records; tests assert absence/safety and do not invoke deleted modules. OpenAI( substring matches AsyncOpenAI construction, not sync runtime. This report itself documents deletion and must not be interpreted as executable references.

| File | Lines / terms | Reason |
|---|---|---|
| `PEM08_V2_SPEC.md` | 46: PyQt6; 73: openai_service; 74: parser_service; 75: history_service; 87: history.json; 104: history.json; 106: OpenAI(; 108: webdriver-manager; 109: @app.on_event; 110: allow_origins=["*"]; 113: PyQt6; 115: innerHTML; 967: innerHTML; 974: innerHTML; 1017: @app.on_event; 1103: webdriver-manager; 1104: history.json; 1105: innerHTML; 1106: PyQt6; 1229: /history, history_service; 1230: history.json; 1231: parser_service; 1233: /analyze_text, /analyze_image, /parse_demo | Historical specification/migration audit, no runtime import |
| `backend/services/ai_service.py` | 60: OpenAI( | AsyncOpenAI constructor (substring match only) |
| `docs/V2_MIGRATION_PLAN.md` | 20: history_service, parser_service, openai_service; 29: history.json; 35: /analyze_text, /analyze_image, /parse_demo, /history, PyQt6; 37: history.json; 55: openai_service; 61: openai_service; 69: parser_service; 77: innerHTML; 79: /history, history_service; 93: allow_origins=["*"]; 99: history_service, openai_service; 105: history_service; 107: history.json; 113: parser_service; 121: history_service, parser_service, openai_service; 123: openai_service; 125: /history; 137: parser_service, openai_service; 143: /history; 165: openai_service; 166: parser_service; 167: /history, history_service; 168: history.json; 199: openai_service; 209: parser_service, selenium; 210: parser_service, webdriver-manager; 211: PyQt6; 289: history_service, parser_service, openai_service; 292: history.json; 306: /history; 333: selenium; 357: backend.models.schemas; 399: openai_service | Historical specification/migration audit, no runtime import |
| `tests/browser_ui_smoke.py` | 498: /analyze_text, /analyze_image, /parse_demo, /history; 510: /history | Negative safety/cutover test or AsyncOpenAI mock construction; no legacy call |
| `tests/startup_smoke.py` | 59: /analyze_text | Negative safety/cutover test or AsyncOpenAI mock construction; no legacy call |
| `tests/test_ai_service_mocked.py` | 123: OpenAI(; 427: OpenAI( | Negative safety/cutover test or AsyncOpenAI mock construction; no legacy call |
| `tests/test_competitors.py` | 180: /analyze_text, /analyze_image, /parse_demo, /history | Negative safety/cutover test or AsyncOpenAI mock construction; no legacy call |
| `tests/test_frontend_contract.py` | 34: innerHTML, outerHTML, insertAdjacentHTML; 35: /analyze_text, /analyze_image, /parse_demo, /history | Negative safety/cutover test or AsyncOpenAI mock construction; no legacy call |
| `tests/test_stage9_hardening.py` | 145: /analyze_text, /analyze_image, /parse_demo, /history; 153: history_service, parser_service, openai_service, backend.models.schemas, selenium, webdriver_manager, PyQt6; 159: history.json; 160: @app.on_event; 162: selenium, webdriver-manager; 165: innerHTML, outerHTML, insertAdjacentHTML, document.write, eval(, new Function; 167: history.json | Negative safety/cutover test or AsyncOpenAI mock construction; no legacy call |

`import re` remains only in URL syntax/security normalization, not AI response parsing.
No active wildcard CORS, @app.on_event, sync OpenAI service, Selenium/driver/desktop import or unsafe frontend sink remains.

## Final Git status (all changes unstaged)

```text
 M .env.example
 M README.md
 M backend/api/analyses.py
 M backend/api/comparisons.py
 M backend/api/competitors.py
 M backend/api/health.py
 M backend/api/sources.py
 M backend/config.py
 M backend/lifespan.py
 M backend/main.py
 M backend/models/__init__.py
 D backend/models/schemas.py
 D backend/services/history_service.py
 D backend/services/openai_service.py
 D backend/services/parser_service.py
 D desktop/CompetitorMonitor.spec
 D desktop/README.md
 D desktop/api_client.py
 D desktop/build.py
 D desktop/main.py
 D desktop/requirements.txt
 D desktop/styles.py
 M docs.md
 D frontend/app.js
 D frontend/legacy-index.html
 D frontend/styles.css
 D history.json
 M requirements-dev.txt
 M requirements.txt
 M run.py
 M tests/browser_ui_smoke.py
 M tests/conftest.py
 M tests/test_ai_service_mocked.py
 M tests/test_competitors.py
 M tests/test_config.py
 M tests/test_frontend_contract.py
 M tests/test_health.py
 D tests/test_history.py
 M tests/test_lifespan.py
 M tests/test_sources.py
 M tests/test_url_sources.py
?? backend/api/errors.py
?? docs/STAGE9_VERIFICATION.md
?? tests/stage9_acceptance.py
?? tests/startup_smoke.py
?? tests/test_stage9_cleanup.py
?? tests/test_stage9_hardening.py
```

Index: empty. HEAD unchanged. No git add/commit/push/reset/branch switch; no Stage 10 started.
Remaining unfinished Stage 9 requirements: NONE.
STOP.
