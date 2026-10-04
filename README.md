# Competitor Intelligence — PEM08 v2

Локальное аналитическое Web workspace: конкуренты → источники → доказательный анализ → сводный анализ → сравнение. FastAPI, статические ES modules без Node/build step, SQLAlchemy/SQLite, AsyncOpenAI, async Playwright и PyMuPDF.

## Установка и запуск (PowerShell)

Проверенная среда: Python 3.14; прямые зависимости закреплены в requirements.txt.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
# Заполните AI_API_KEY и параметры вашего OpenAI-compatible провайдера.
python run.py
```

Единственная команда запуска приложения: `python run.py`. Откройте http://127.0.0.1:8000. API: `/api/v2`, Swagger: `/docs`, ReDoc: `/redoc`, схема: `/openapi.json`, health: `/api/v2/health`.

Без ключа сервер и workspace запускаются, health честно сообщает `ai_configured=false`; новые AI-анализы недоступны. Наличие ключа не означает, что провайдер доступен или поддерживает выбранную модель/structured output/vision. Рабочий провайдер должен поддерживать `chat.completions.parse` с полными Pydantic schemas.

## Workflow

- Создайте конкурентов: название, сайт, ниша, заметки; редактирование и удаление доступны в UI.
- Добавьте текст (10–30 000 символов), JPEG/PNG/WebP, PDF или HTTP(S) URL. Источник сохраняется в SQLite и автоматически анализируется.
- Изображение анализируется визуально; PDF обрабатывается PyMuPDF: извлечённый текст и изображения выбранных страниц. Длинные PDF анализируются частично с явными limitations.
- URL открывается Chromium через Playwright: итоговый URL, title, meta description, видимый текст и PNG screenshot сохраняются как snapshot. Длинный текст обрезается с limitations.
- «Повторить анализ» использует текущий сохранённый snapshot и создаёт новый analysis; URL повторно не загружается.
- «Обновить страницу» доступно только для URL: новый capture, новый snapshot, новый analysis. Предыдущие snapshots сохраняются.
- «Сводный анализ» объединяет последние analyses текущих snapshots конкурента; `analysis_type=aggregate`, `snapshot_id=null`. Старый сводный результат можно открыть без нового AI-запроса.
- «Сравнить» принимает 2–5 разных конкурентов с уже сохранённым сводным анализом; UI показывает одинаковые критерии, strengths/gaps и limitations. Скрытого создания aggregate и вычисления winner нет.
- Executive summary, scorecard с rationale, overview, evidence/provenance, actions и limitations показываются безопасным plain text. История analyses доступна в workspace.

## Persistence и артефакты

SQLite (`DATABASE_URL`) хранит конкурентов, источники, snapshots, analyses и comparisons и сохраняет их после перезапуска. Оригинальные uploads и PNG screenshots лежат в отдельных каталогах. Сохраняйте резервную копию SQLite вместе с обоими каталогами при остановленном приложении. Историческое comparison остаётся после удаления конкурента; остальные связанные записи и принадлежащие им файлы удаляются.

Preview изображения/URL screenshot выдаётся только через DB-привязанный `/api/v2/sources/{source_id}/snapshots/{snapshot_id}/artifact`: UUID-файл, проверенный resolver, без клиентского filesystem path. PDF preview — метаданные, извлечённый текст и информация о страницах; inline PDF serving не используется.

При AI failure уже сохранённые source/snapshot и артефакты остаются для retry. До успешного сохранения snapshot ошибки DB/ingestion убирают новые файлы. При неуспешном удалении DB rollback восстанавливает удалённые файлы; посторонние файлы не затрагиваются.

## Конфигурация

`.env.example` содержит действующие параметры. `.env` исключён из Git.

| Параметры | Назначение |
|---|---|
| APP_ENV, APP_HOST, APP_PORT | `development` включает reload; другое значение отключает reload. По умолчанию 127.0.0.1:8000. API_HOST/API_PORT поддерживаются как необязательные aliases; предпочтительны APP_* |
| AI_PROVIDER, AI_API_KEY, AI_BASE_URL, AI_MODEL | Провайдер/ключ/base URL/идентификатор модели; defaults в `.env.example`, доступность зависит от провайдера |
| AI_REASONING_EFFORT, AI_TIMEOUT_SECONDS | Reasoning effort; пустое значение не отправляется. Timeout в секундах |
| DATABASE_URL, UPLOAD_DIR, SCREENSHOT_DIR | SQLite и каталоги артефактов; по умолчанию `data/` |
| MAX_IMAGE_MB, MAX_PDF_MB | Ограничения upload: 10 / 25 MiB |
| MAX_TEXT_CHARS, MAX_WEB_TEXT_CHARS | Лимиты текста: 30 000 / 25 000 |
| MAX_PDF_PAGES_ANALYZED | По умолчанию 8 выбранных страниц, включая начало/конец |
| BROWSER_HEADLESS, BROWSER_TIMEOUT_MS | Chromium headless и timeout capture (20 000 ms) |
| BROWSER_VIEWPORT_WIDTH, BROWSER_VIEWPORT_HEIGHT | Viewport capture 1440 × 1200 |
| CORS_ORIGINS | Список точных HTTP(S) origins через запятую. Default только localhost; пустое значение отключает cross-origin доступ. Wildcard запрещён, credentials отключены |
| LOG_LEVEL | DEBUG/INFO/WARNING/ERROR/CRITICAL |

## Границы безопасности и ограничения

Core предназначен для локальной работы доверенного пользователя: authentication отсутствует. APP_HOST по умолчанию loopback; публикация в общедоступной сети требует отдельной инфраструктуры доступа. CORS не заменяет authentication.

URL policy блокирует private/loopback/link-local/metadata/multicast/reserved адреса, credentials и неожиданные schemes; redirects и subrequests повторно проверяются. Capture не выполняет login и не обходит CAPTCHA/paywall. Это ограниченный browser capture публичных страниц; сетевой egress control остаётся дополнительной инфраструктурной границей. AI вывод может быть неполным или ошибочным: evidence и limitations следует проверять.

Все ошибки API используют HTTP statuses и `{"error":{"code":"...","message":"...","details":null}}`: 400 invalid operation, 404 missing resource, 413 oversized upload, 422 validation, 502 provider failure, 504 timeout, 500 internal failure. CORS preflight — стандартный протокол middleware, не JSON API response. Клиенту не выдаются исключения, secrets или пути. Сервер пишет method, шаблон route, status, duration и code; для unexpected failure — тип и stack frames без значения исключения, locals или request body. Access logging Uvicorn отключён, query/header/document/binary/AI payload не логируются.

## Проверки

```powershell
python -m pip install -r requirements-dev.txt
python -m pip check
python -m pytest -q
python -m tests.stage9_acceptance
python -m tests.browser_local_smoke
python -m tests.browser_ui_smoke
python -m tests.startup_smoke
python -m compileall -q backend tests
```

Все acceptance/smoke проверки offline, с временными SQLite/uploads/screenshots; production `data/` не используется. AI заменён детерминированными fakes; browser UI capture fake, отдельный local Chromium smoke проверяет реальный capture. JS syntax при доступном Node: `Get-ChildItem frontend/js/*.js | ForEach-Object { node --check $_.FullName }` (Node не runtime dependency).

Полный cutover inventory и результаты: [docs/STAGE9_VERIFICATION.md](docs/STAGE9_VERIFICATION.md). Спецификация: [PEM08_V2_SPEC.md](PEM08_V2_SPEC.md); исторический поэтапный план: [docs/V2_MIGRATION_PLAN.md](docs/V2_MIGRATION_PLAN.md).
