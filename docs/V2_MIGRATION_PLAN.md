# V2 Migration Plan

Дата аудита: 2026-10-02. Проект: Competitor Intelligence Assistant v2.

Главный контракт: `PEM08_V2_SPEC.md`. Это результат аудита и предложение реализации, а не выполненная миграция. В рамках аудита создаётся только этот документ. Production-код, зависимости, Git index и история не изменяются.

## 1. Current repository baseline

- Корень: `C:\Users\admin\CodexWorkspace\pem08-v2`.
- Ветка: `develop-v2`.
- HEAD: `4a0ebe150d19ac9cf79f95e3044fb19909edadef` — `docs: add v2 implementation specification`.
- До аудита `git status --short` пустой. В репозитории 27 tracked-файлов, 14 файлов Python, один PyInstaller spec. Локальных `AGENTS.md`, тестов, CI, lock-файла и проектного virtualenv не обнаружено. Применены инструкции пользователя из сессии.

```text
backend/
  __init__.py
  config.py
  main.py
  models/{__init__.py,schemas.py}
  services/{__init__.py,openai_service.py,parser_service.py,history_service.py}
frontend/{index.html,styles.css,app.js}
desktop/
  main.py, api_client.py, styles.py, build.py
  CompetitorMonitor.spec, requirements.txt, README.md
.gitignore
requirements.txt
run.py
env.example.txt
history.json
README.md
docs.md
PEM08_V2_SPEC.md
```

Архитектура v1: `run.py → Uvicorn → backend.main:app`. FastAPI отдаёт `/`, `/static`, `/docs`, `/redoc`, `/health` и независимые операции `/analyze_text`, `/analyze_image`, `/parse_demo`, GET/DELETE `/history`. API вызывает глобальные экземпляры сервисов. AI — синхронный OpenAI SDK с ProxyAPI base URL. URL capture — новый Selenium Chrome на каждый запрос, работающий через executor. История — краткие записи в JSON. Web UI — один JS-файл без сборки; PyQt6-клиент повторяет те же сценарии через HTTP и требует отдельно запущенный backend.

`history.json` содержит 6 записей, соответствующих `HistoryItem`: id, timestamp, request_type, request_summary, response_summary. Полные исходники и результаты там не сохранены; содержимое пользовательских записей в отчёт не копировалось.

Основа FastAPI / Vanilla JS пригодна. Основной domain pipeline v2 отсутствует: нет competitors, sources, snapshots, SQLite, evidence, унифицированной scorecard, PDF, reanalyze, refresh, aggregate и compare. Существующий класс `CompetitorAnalysis` имеет другое содержание и не совместим с одноимённым контрактом v2.

Для security-части прочитаны:

- `C:/Users/admin/.codex/skills/security-best-practices/SKILL.md`;
- `C:/Users/admin/.codex/skills/security-best-practices/references/python-fastapi-web-server-security.md`;
- `C:/Users/admin/.codex/skills/security-best-practices/references/javascript-general-web-frontend-security.md`.

Проверка ориентирована на локальный однопользовательский MVP: авторизация и cloud deployment не добавляются как новые обязательные требования.

## 2. Findings

Уровень отражает приоритет исправления при миграции. Статически подтверждённый путь данных не означает проведённую эксплуатацию: сетевые атаки, платные AI-запросы и browser capture не запускались.

### F01 — High: блокировка event loop AI-вызовами

`backend/services/openai_service.py:12,33,68,96,134,163,245,303`: синхронный `OpenAI` и `self.client.chat.completions.create(...)` вызываются непосредственно внутри `async def`. `await openai_service.analyze_*` в `backend/main.py` не делает вложенный sync HTTP-вызов асинхронным. Долгий AI-запрос задерживает остальные задачи этого worker. Явных настроек timeout/retry приложения нет; это не утверждение об отсутствии SDK defaults.

План: `AsyncOpenAI` в `ai_service.py`, timeout/retry из config, разделение transport/domain, закрытие клиента в lifespan. Mock-тест должен подтверждать await и параллельное обслуживание независимого запроса.

### F02 — High: невалидный AI-ответ превращается в успешный пустой результат

`openai_service.py:43–66`: regex извлекает JSON, `JSONDecodeError` возвращает `{}`. Затем `:115–121,195–201,335–341` подставляют пустые значения; image score при этом становится 5. `backend/models/schemas.py:23–38` допускает пустые анализы. API возвращает `success=True` и сохраняет краткую запись. Pydantic применяется, но не предотвращает этот сценарий. Локально подтверждено создание пустых обеих моделей; сам SDK не запускался.

Также не разобраны refusal, пустой content, finish_reason и truncation; обращения к `choices[0]`/`len(content)` могут аварийно завершиться. Промпты требуют фиксированное число выводов, но не требуют evidence/limitations, что создаёт риск домысливания.

План: strict JSON Schema и финальная Pydantic validation согласно ADR-04, явные ошибки без успешного fallback. Сохранить имена и смысл полей §9 ТЗ. Проверить преобразование Pydantic schema в provider-compatible strict schema: required nullable поля, запрет лишних свойств, поддерживаемые ограничения. Протестировать malformed JSON, пустой результат, refusal, timeout и score вне диапазона.

### F03 — High: SSRF boundary отсутствует (FASTAPI-SSRF-001)

`backend/models/schemas.py:16–18`: `url: str`; `parser_service.py:179–197` только добавляет `https://`, затем `_parse_sync:86` вызывает `driver.get(url)`. Нет запрета localhost/private/link-local, DNS/IP-проверок, контроля redirect и подресурсов. Локальная проверка подтвердила, что request schema принимает `http://127.0.0.1`.

Воздействие: клиент API может инициировать браузерное обращение из сети backend, в том числе к внутреннему адресу; захваченный контент потенциально передаётся AI-провайдеру. Реальная доступность внутренних сервисов не проверялась.

План: `security/url_validation.py` до навигации; разрешить только http/https, проверять hostname, все A/AAAA-адреса, IPv4/IPv6 и redirect destinations. Защита должна применяться и к browser subrequests до их отправки: проверка final URL после загрузки запаздывает. DNS rebinding требует отдельного тестирования и ограничения фактического egress; один предварительный resolve не даёт полной гарантии. До исправления — локальный bind и только доверенные URL как временное снижение риска, не замена валидатора.

### F04 — High: небезопасный DOM rendering (JS-XSS-001)

`frontend/app.js:126` вставляет html в `resultsContent.innerHTML`; `:164,213–225,249` включают AI summary, URL, title, H1, абзац и элементы списков без escaping. `:290–308` вставляет `request_summary` из JSON history в `innerHTML`. Ошибки в `showError` также интерполируются в HTML.

Путь user text → `backend/main.py:133–136` → `history_service.py:89` → `/history` → `renderHistory` подтверждён кодом. Риск — stored/DOM XSS в origin приложения; browser exploit не выполнялся. Статические SVG сами по себе не являются finding.

План: строить динамические элементы через `createElement`/`textContent`, проверять URL отдельно при назначении href/src. До замены не использовать старый rendering для новых v2 данных. Проверка: HTML-подобный текст отображается буквально, не создаёт элементы/обработчики.

### F05 — High: загрузки и текст не ограничены приложением (FASTAPI-UPLOAD-001 / LIMITS-001)

`backend/main.py:165–184`: проверяется только заявленный Content-Type, принимается GIF, весь файл читается через `await file.read()` и дополнительно копируется в base64. Нет проверки реального формата, длины и размеров изображения. `frontend/index.html:121` и `docs.md:261` обещают 10MB, но `frontend/app.js` также не проверяет `file.size`. `schemas.py:13` задаёт только минимальную длину текста; 30001 символ локально принят.

Риски: расход памяти/диска multipart spool, неограниченная AI-нагрузка и ошибочный MIME. Не утверждается обход конкретных framework multipart defaults — они runtime не проверялись.

План: bounded чтение с прекращением по лимиту, ограничения тела/multipart, 413 до AI; JPEG/PNG/WebP с Pillow verify и лимитом пикселей, PDF signature/open validation, SHA-256, генерируемые storage filenames, исходное имя только metadata. Для текста — 30000 символов, web text — 25000, PDF — 25MB/8 анализируемых страниц, image — 10MB согласно config ТЗ. Синхронные decoding/rendering/hash/file операции не выполнять длительно на event loop.

### F06 — Medium: permissive CORS и сетевой bind (FASTAPI-CORS-001)

`backend/main.py:45–50`: `allow_origins=["*"]`, credentials/methods/headers разрешены широко. `backend/config.py:52` и `env.example.txt:18` задают `0.0.0.0`. Отсутствие авторизации допустимо для целевого local MVP, но binding расширяет поверхность доступа. CORS не защищает от прямых HTTP-клиентов; конкретное поведение браузера с credentials не проверялось.

План: default `127.0.0.1`, CORS allowlist из config для двух localhost origins; credentials выключены, если не требуются. Проверить разрешённый и чужой Origin. Не добавлять авторизацию вопреки scope.

### F07 — High: `.env` не исключён из Git; Medium: логи содержат лишние данные

`.gitignore:1–22` не содержит `.env`; `git check-ignore .env` вернул 1. Это риск будущего добавления секрета, а не обнаруженная утечка: tracked `.env` отсутствует. В `openai_service.py:30` логируется суффикс ключа; `main.py:121,228`, `openai_service.py:73`, `history_service.py:80–81` логируют input/URL/summary. URL может содержать чувствительные query parameters.

План Stage 1: `.env` ignore с разрешённым `.env.example`, secret без вывода даже суффикса, безопасные сообщения/operation_id; redaction URL и ошибок. Полные документы/base64 не логировать. Проверить logs с тестовым маркером секрета. Не объявлять текущую историю Git проверенной на все возможные секреты — такого сканирования не было.

### F08 — High для целевого workflow: JSON history не обеспечивает persistence v2

`history_service.py:48–68,83–103`: синхронный read-modify-write целого файла, без atomic replace, locking и транзакций. Потеря обновлений возможна при нескольких процессах; прерванная запись может повредить файл. При JSON corruption загрузка возвращает `[]`, последующая запись может перезаписать старые данные. `datetime.now()` не задаёт UTC. В async handlers выполняется sync файловый I/O.

Сохраняются только обрезанные summaries и максимум 10 записей; нет воспроизводимых inputs, полной версии анализа, model_id, prompt_version, токенов, snapshot. `history.json` tracked.

План: SQLite repositories и disk storage по §8 ТЗ. Старую историю сохранить до DoD/архивирования. Не создавать из summaries фиктивные competitors, sources или evidence: автоматическая полноценная миграция данных невозможна. Если позже нужен legacy import, это отдельная операция с явной маркировкой неполных данных.

### F09 — Medium: Selenium overhead и lifecycle

`parser_service.py:36,191–197`: Selenium уже вынесен в `ThreadPoolExecutor(max_workers=2)`; `time.sleep(2)` на строке 99 выполняется в этом потоке, а не в event loop. Тем не менее на каждый capture создаётся Chrome и вызывается `ChromeDriverManager().install()` (`:60–62`), возможны сетевое получение драйвера и зависимость от Chrome/driver/cache. В очереди executor нет прикладного лимита.

`driver.quit()` есть в finally. `close():210` использует `shutdown(wait=False)`, не гарантирует завершения активных capture до shutdown приложения. Включены `--no-sandbox` и настройки скрытия automation (`:48,53–55`), которые не следует переносить в v2.

План: один async Playwright browser на lifespan, отдельный закрываемый context на capture, ограниченная конкуренция/timeout и cleanup при отмене. Зависимость browser binaries устанавливать отдельно при этапе URL. Никаких stealth/anti-bot обходов.

### F10 — Medium: import-time side effects и неполный shutdown

`backend/services/__init__.py:1–3` eagerly импортирует все сервисы; `openai_service.py:362`, `parser_service.py:216`, `history_service.py:130` создают singleton при импорте. `HistoryService.__init__` может создать файл. `config.py:10,39,68` загружает окружение, настраивает глобальные логи и settings при импорте. Даже health зависит от импортируемого AI/browser стека.

Последующий smoke test в отдельном Python 3.14.0 virtual environment с установленными baseline dependencies подтвердил дефект (результаты предоставлены пользователем, см. §8): при импорте `backend.services.openai_service` выполняется `openai_service = OpenAIService()`, а `OpenAIService.__init__()` сразу создаёт OpenAI client (`openai_service.py:33–36`). Без API key SDK выбрасывает `openai.OpenAIError: Missing credentials`. Поэтому `backend.main` невозможно импортировать без AI credentials; по той же причине локальный `/health` smoke test без ключа заблокирован. Причина — обязательная import-time инициализация клиента, не Python 3.14 и не отсутствующая зависимость. `/health` не должен зависеть от наличия AI credentials.

`main.py:81,93` использует startup/shutdown events, shutdown закрывает только parser. Пути frontend/history и `.env` относительны CWD (`main.py:110,350`, `config.py:56,64`); запуск из другой директории хрупок. `run.py:34` всегда reload=True — допустимо для development, но режим не настраивается.

План: lifespan, минимальные `__init__`, ресурсы в app state/dependencies, пути относительно project root, закрытие всех созданных ресурсов, тесты repeated startup/shutdown и partial-start failure. Убрать обязательное создание AI client при импорте: безопасная lazy initialization или dependency-based initialization только для операций, которым нужен AI. Импорт FastAPI application и `/api/v2/health` должны работать без ключа и без обращения к AI provider; закрепить это автоматическим regression test. Перенос безусловного создания клиента из import в startup сам по себе проблему health без ключа не решает. Исправление здесь не реализуется.

### F11 — Medium: ошибки скрываются за HTTP 200

`main.py:146–152,212–218,240–246,304–310` возвращает `success:false` с HTTP 200 и raw `str(e)`. Ошибка записи истории после успешного AI выглядит как общий failure и может привести к повторному платному запросу. Middleware считает такой HTTP 200 успехом. `frontend/app.js:46–90` не проверяет `response.ok`; clear-history UI может показывать очистку без подтверждения успеха.

План: единый envelope и коды 400/404/413/422/502/504/500 по §13; безопасное сообщение клиенту, диагностическая причина в server log без секретов. Отдельно определить результат частичной операции: source/snapshot сохраняются, невалидный анализ не создаётся; последующий reanalyze должен быть возможен.

### F12 — Medium: URL-анализ теряет контекст и неверно маркирует screenshot

`parser_service.py:101–140` извлекает title, один H1, первый подходящий абзац до 500 символов и PNG screenshot. Нет H2, meta description, visible body text, final URL и хранения capture. `main.py:278–283` возвращает исходный URL. `openai_service.py:317` обозначает эти PNG-байты как `data:image/jpeg`. Это подтверждённое MIME-несоответствие; реакция провайдера не проверена.

План: Playwright snapshot по §11.4, корректный MIME, ограничение текста, origin metadata, screenshot paths; reanalyze использует текущий snapshot, refresh создаёт новый snapshot и новый анализ.

### F13 — Medium: дублирующий desktop и расхождения документации

Все файлы `desktop/` обслуживают старые четыре сценария. Основной analysis выполняется через QThread, однако `main.py:454–461,670–672,733–744` вызывает health/history/delete синхронно в GUI-потоке. `desktop/api_client.py:50–64` маркирует любой image как JPEG. `.exe` не включает backend, что честно указано в desktop README. `build.py clean` удаляет также `*.spec`; этот скрипт не запускался.

`README.md:43` предлагает `OPENAI_API_KEY`, а config читает `PROXY_API_KEY`; README называет BeautifulSoup/httpx parser (`:117`), тогда как реализация Selenium. README говорит Python 3.9+, целевой контракт требует 3.12+. PDF отсутствует в коде и UI; обещание PDF упомянуто в ТЗ, но в текущих README/docs отдельного заявления о реализованном PDF не найдено.

Web UI требует переработки из вкладок в workspace; внешний Google Fonts в `index.html:7–9`, glow theme и блокирующий overlay расходятся с §14–15. CSS primitives можно сохранить, layout/state/rendering необходимо изменить.

### F14 — Medium: отсутствует воспроизводимая проверка baseline

Тестов, test config и CI нет. Requirements задают только старые нижние границы без зафиксированного проверенного набора. При первоначальном аудите в доступном Python отсутствовала значительная часть runtime-зависимостей, импорт падал на `pydantic_settings`. В последующем отдельном Python 3.14.0 venv все зависимости текущего requirements.txt успешно установились и compileall прошёл; оставшийся import blocker — `Missing credentials` из F10. Подтверждённой несовместимости Python 3.14 сейчас нет; полноценная совместимость требует тестов v2.

## 3. Reuse matrix

`reuse` — почти без изменений; `refactor` — сохранить идею; `replace` — заменить реализацию; `remove-later` — вне v2, пока сохранить; `new` — отсутствует. Все решения ниже относятся к будущей миграции.

| Current component | Decision | Target v2 component | Reason |
|---|---|---|---|
| `backend/__init__.py` | reuse | тот же package marker | Нет runtime логики |
| `backend/main.py` | refactor | app assembly + `api/*` | FastAPI/static/OpenAPI сохранить; вынести routes/lifecycle |
| `backend/config.py` | refactor | config v2 | Settings сохранить, ключи/пути/лимиты/логирование заменить |
| `backend/models/__init__.py` | refactor | явные domain exports | Убрать wildcard legacy schemas |
| `backend/models/schemas.py` | replace | `models/api.py`, `models/analysis.py` | Имена/семантика v1 не соответствуют §9 |
| `backend/services/__init__.py` | refactor | package без eager singleton imports | Независимые imports/testability |
| `backend/services/openai_service.py` | replace | `ai_service.py`, `prompt_service.py`, `analysis_service.py` | Async, строгая schema, evidence, orchestration |
| `backend/services/parser_service.py` | replace | `browser_service.py`, `security/url_validation.py` | Playwright snapshots и SSRF |
| `backend/services/history_service.py` | replace | repositories + SQLite | Полные данные, транзакции, версии |
| `history.json` | remove-later | не runtime storage v2 | Сохранить legacy данные до DoD |
| `frontend/index.html` | refactor | workspace shell | HTML/без build-step сохранить |
| `frontend/styles.css` | refactor | dashboard theme/layout | Reuse tokens/forms, заменить glow/overlay/layout |
| `frontend/app.js` | replace | ES modules: app/api/state/render | Старые routes/state и unsafe HTML |
| `desktop/main.py` | remove-later | только Web UI | Второй интерфейс вне core MVP |
| `desktop/api_client.py` | remove-later | `frontend/api.js` | Клиент legacy routes |
| `desktop/styles.py` | remove-later | Web CSS | Qt theme не нужна |
| `desktop/build.py` | remove-later | `python run.py` | EXE вне core |
| `desktop/CompetitorMonitor.spec` | remove-later | отсутствует | Build artifact/config legacy |
| `desktop/requirements.txt` | remove-later | core requirements | Qt/build зависимости не переносить |
| `desktop/README.md` | remove-later | legacy archive docs | Не документирует v2 |
| `run.py` | refactor | единый launcher | Loopback/config/reload и надёжные пути |
| `requirements.txt` | refactor | проверенный стек §5 | Заменить browser deps, добавить persistence/PDF/tests |
| `env.example.txt` | replace | `.env.example` | Ключи AI_*/APP_*/limits по ТЗ |
| `.gitignore` | refactor | исключение secrets/runtime data | Сейчас отсутствуют `.env`, DB, uploads |
| `README.md`, `docs.md` | refactor | точное руководство v2/API | Устранить расхождения |
| `PEM08_V2_SPEC.md` | reuse | главный контракт | Не менять при аудите |
| отсутствует | new | lifespan, API routers, models/db, DB/session/init | Foundation/CRUD |
| отсутствует | new | repositories competitors/sources/analyses/comparisons | Persistence и transactions |
| отсутствует | new | storage/document/ingestion services | Files/PDF/единый prepared input |
| отсутствует | new | comparison service | Aggregate/compare 2–5 |
| отсутствует | new | tests, data directories, project AGENTS.md | Проверяемость и инструкции по §7 |

## 4. Dependency audit

Решения KEEP/UPDATE/REMOVE/ADD — план, не выполненные pip-операции. UPDATE означает пересмотр нижней границы и подбор проверенного совместимого диапазона, а не безусловную установку latest. Старый минимум `>=` не доказывает, что именно старая версия установлена. Полный vulnerability scan и resolver/installation test не выполнялись.

| Dependency / declared minimum | Фактическое использование v1 | Decision | Действие для v2 |
|---|---|---|---|
| fastapi >=0.104.0 | `backend/main.py` | UPDATE | Lifespan/routers; поднять baseline для поддерживаемого Python |
| uvicorn >=0.24.0 | `run.py`, `main.py` | UPDATE | Проверить lifecycle/reload/Windows loop с выбранной версией |
| openai >=1.6.0 | `openai_service.py` | UPDATE | AsyncOpenAI и strict structured output; проверить SDK/provider пару |
| httpx >=0.25.0 | Прямых imports нет; transport SDK | UPDATE | Оставить для SDK и ASGITransport/TestClient/mocks, совместимые версии |
| python-multipart >=0.0.6 | Неявно нужен `UploadFile`/`File` | UPDATE | Актуальный проверенный multipart stack с лимитами |
| beautifulsoup4 >=4.12.0 | Не используется | REMOVE | DOM extraction Playwright, после перехода |
| lxml >=5.0.0 | Не используется | REMOVE | Не нужен browser pipeline |
| pydantic >=2.5.0 | `models/schemas.py` | UPDATE | Оставить v2, strict domain contracts; проверить Python 3.14 |
| pydantic-settings >=2.1.0 | `config.py` | UPDATE | SettingsConfigDict, AI/config поля и CSV CORS parser |
| python-dotenv >=1.0.0 | `config.py:8,10` | KEEP | Нужен `.env`; убрать дублирующий load_dotenv при переходе на Settings |
| aiofiles >=23.2.0 | Не используется | REMOVE | Не добавлять использование ради зависимости; bounded file I/O в worker |
| Pillow >=10.0.0 | Не используется, включая desktop | UPDATE | Нужен v2 для verify/thumbnail/metadata; baseline для 3.14 пересмотреть |
| selenium >=4.15.0 | `parser_service.py` | REMOVE | Только после замены URL flow |
| webdriver-manager >=4.0.0 | `parser_service.py` | REMOVE | Только после замены URL flow |
| PyQt6 >=6.6.0 (desktop) | `desktop/main.py` | REMOVE | Убрать вместе с desktop после DoD, не часть core |
| requests >=2.31.0 (desktop) | `desktop/api_client.py` | REMOVE | Убрать прямую desktop зависимость после архивирования; не ломать legacy раньше |
| Pillow >=10.0.0 (desktop) | Нет imports PIL | REMOVE | Удалить desktop declaration вместе с legacy; core Pillow оставить |
| pyinstaller >=6.0.0 (desktop) | `desktop/build.py`, spec | REMOVE | EXE не нужен v2 |
| SQLAlchemy 2.x | Отсутствует | ADD | SQLite models/session/repositories, этап 2 |
| playwright | Отсутствует | ADD | Async browser, этап 6; Chromium отдельный install |
| PyMuPDF | Отсутствует | ADD | PDF extraction/rendering, этап 5; не пакет `fitz` |
| pytest | Отсутствует | ADD | Test foundation, этап 1 |
| pytest-asyncio | Отсутствует | ADD | Async tests, этап 1 |
| sqlite3 | Python stdlib | KEEP | Отдельный pip-пакет не нужен |

Предложение для SQLAlchemy: сохранить `sqlite:///./data/app.db` из ТЗ, синхронные короткие repository units выполнять вне event loop, не делить Session между потоками и не держать транзакцию во время AI/browser I/O. `aiosqlite` не добавлять автоматически: AsyncEngine потребует отдельного обоснования и изменения URL-конфигурации. Для файлов достаточно bounded sync работы через уже доступный thread offload, без новой зависимости aiofiles.

В интерпретаторе первоначального аудита были установлены FastAPI 0.135.2, Uvicorn 0.42.0, Pydantic 2.12.5, requests 2.32.5. Остальные перечисленные внешние пакеты отсутствовали, включая httpx, OpenAI, multipart и pytest. Это исторический снимок того окружения. Позднее в отдельном Python 3.14.0 venv успешно установлены все зависимости текущего корневого `requirements.txt` (см. §8); это не означает установку desktop requirements или новых зависимостей v2. Точные resolved versions этого venv не предоставлены.

Устарели прежде всего нижние границы и интеграционный подход, а не все библиотеки как продукты. FastAPI документирует поддержку Python 3.14 начиная с 0.118.3; исходный минимум 0.104.0 её не обеспечивает ([release notes](https://fastapi.tiangolo.com/release-notes/#01183)). Pillow 12.0.0 официально добавил Python 3.14, поэтому минимум 10.0.0 недостаточен как гарантия ([release notes](https://pillow.readthedocs.io/en/stable/releasenotes/12.0.0.html)). Эти версии — свидетельство необходимости обновить baseline, не окончательные pins. Для остальных зависимостей точные новые версии следует выбрать и проверить в изолированном venv на этапе реализации.

## 5. Target file map

Сейчас создаётся только `docs/V2_MIGRATION_PLAN.md`. Следующая карта — будущие изменения, не список выполненных работ.

**Сохранить без смысловой переработки:** `PEM08_V2_SPEC.md`, `backend/__init__.py`. Legacy-файлы сохраняются до соответствующих проверок.

**Изменить на последующих этапах:**

- Stage 1: `backend/config.py`, `backend/main.py`, `backend/services/__init__.py` (развязать eager imports), `run.py`, `.gitignore`, `requirements.txt` (только необходимый foundation набор), `README.md`.
- По мере подключения domain/UI: `backend/models/__init__.py`, `frontend/index.html`, `frontend/styles.css`, `frontend/app.js` (замена содержимого), `docs.md`, `README.md`, `requirements.txt`.

**Создать по структуре §7 ТЗ:**

```text
backend/lifespan.py
backend/api/__init__.py
backend/api/health.py
backend/api/competitors.py
backend/api/sources.py
backend/api/analyses.py
backend/api/comparisons.py
backend/models/api.py
backend/models/analysis.py
backend/models/db.py
backend/repositories/competitors.py
backend/repositories/sources.py
backend/repositories/analyses.py
backend/repositories/comparisons.py
backend/services/ai_service.py
backend/services/prompt_service.py
backend/services/browser_service.py
backend/services/document_service.py
backend/services/ingestion_service.py
backend/services/analysis_service.py
backend/services/comparison_service.py
backend/services/storage_service.py
backend/security/url_validation.py
backend/db/session.py
backend/db/init_db.py
frontend/api.js
frontend/state.js
frontend/render.js
tests/conftest.py
tests/test_health.py
tests/test_competitors.py
tests/test_sources.py
tests/test_analysis_schemas.py
tests/test_url_validation.py
tests/test_ai_service_mocked.py
.env.example
AGENTS.md
data/.gitkeep
```

Пустые `__init__.py` в новых Python packages допустимы по выбранной package convention. Дополнительные целевые проверки: `tests/test_config.py`, `tests/test_lifespan.py`, `tests/test_document_service.py`, `tests/test_comparisons.py` и optional локальная browser fixture/test. Не создавать заранее пустую реализацию всех этапов.

Runtime создаёт `data/app.db`, `data/uploads/`, `data/screenshots/`; БД, WAL/SHM/journal, uploads/screenshots не коммитятся. В `.gitignore` оставить возможность отслеживать `.env.example` и `data/.gitkeep`. `.env` — локальный пользовательский файл, не генерировать с ключом.

**Удалить/архивировать позже, только после зелёного DoD:**

- `backend/models/schemas.py` после исчезновения всех legacy imports;
- `backend/services/openai_service.py`, `parser_service.py`, `history_service.py` после переключения потребителей;
- старые v1 routes внутри `main.py` после проверки нового Web UI;
- `env.example.txt` после актуализации инструкций на `.env.example`;
- `history.json` после сохранения legacy данных пользователем; не считать его переносимым полным dataset;
- весь `desktop/`, включая семь перечисленных файлов, в `legacy/desktop-v1/` либо удалить по отдельному выбранному способу сохранения.

301 redirects не решают совместимость POST с изменённым request/response contract. Compatibility adapter нужен только при реальном требовании, отсутствующем сейчас.

## 6. Migration sequence

Каждый шаг имеет отдельный проверяемый результат. Работать в текущей разрешённой ветке; новый branch/commit не является частью этого аудита. Удаления отложены до финальной проверки. Безопасность upload/URL/rendering включается одновременно с соответствующим pipeline, а не откладывается целиком на hardening.

| Шаг | Изменение | Проверка / критерий выхода |
|---|---|---|
| 1. Foundation | Config, lifespan, health, минимальный test setup; см. §9 | Import без key/сети; health 200 с честными состояниями; config и shutdown tests |
| 2. Persistence + CRUD | Models пяти таблиц §8, session/init, competitors repository/API; остальные repositories добавлять с их потребителями | Temp SQLite: create/list/get/patch/delete, 404/422, UTC, FK, rollback; отсутствие записи в реальную data |
| 3. AI contract | CompetitorAnalysis/ComparisonResult, PreparedAnalysisInput, prompt version, AsyncOpenAI transport | Mocked text + multimodal request shape; strict schema, отказ/timeout/invalid JSON; нет regex и пустого success |
| 4a. Text sources | Source/snapshot, ingestion, orchestration и сохранение analyses | Auto-analysis после добавления, latest analysis/history, reanalyze создаёт новую analysis для текущего snapshot; ошибка AI не теряет источник |
| 4b. Images | Bounded upload, verify, hash, generated path, metadata/thumbnail, multimodal input | Wrong MIME/signature/GIF → 400; oversized → 413 до AI; валидный image сохраняется; cleanup ошибки |
| 5. PDF | PyMuPDF extraction + выбранные страницы | Текстовый и scanned fixture; первая страница всегда включена, максимум 8, равномерная выборка; limitations при частичном анализе; повреждённый/encrypted документ — контролируемая ошибка |
| 6a. URL safety | URL/DNS/redirect/subrequest policy | Mock DNS: localhost/.local/private/link-local/IPv6/mixed answers блокируются; public HTTPS проходит; external sites в unit suite отсутствуют |
| 6b. Browser | Общий Chromium, отдельные contexts, snapshot и refresh | Optional local fixture с ограниченным test-only разрешением; capture метаданных/text/PNG; закрытие на успех/ошибку/отмену; refresh создаёт snapshot + analysis |
| 7. Aggregate/compare | Сводка активных источников, сравнение 2–5, сохранение comparison | Неизвестные ID → 404, один/шесть ID → 422; duplicate IDs не должны имитировать нескольких конкурентов; одинаковые criteria, limitations при недостатке данных |
| 8. Web workspace | ES modules, competitors/sources/analysis, evidence/rationale/actions/limitations и compare | Все сценарии доступны без Swagger; безопасный DOM, preview, CRUD/reanalyze/refresh; local browser console/network/loading/responsive проверка |
| 9. Hardening + cutover | Error envelope, logging, CORS и cleanup regression, dependency baseline, README/docs, removal legacy | Весь pytest suite, single-command startup, 3 competitors, все четыре source types, restart persistence и полный §21 DoD |

API сохраняет пути и семантику §10: competitors CRUD; text/file/url auto-analysis; source get/delete/reanalyze/refresh; analyses list/get; aggregate-analysis; POST comparisons. Не заменять их четырьмя v1 analyzer endpoints.

Решения, которые нужно зафиксировать перед соответствующим шагом:

- Удаление competitor/source: FK/cascade и связанные файлы/snapshots/analyses; как сохранить смысл ранее созданных comparisons после удаления competitor. ТЗ не даёт полного правила retention.
- Upload/snapshot и AI failure — раздельные короткие транзакции; source остаётся доступным для retry, analysis добавляется только после validation. Ошибка файла/БД требует cleanup без удаления чужих файлов.
- Reanalyze — не новый capture; URL refresh — новый snapshot. Aggregate помечается `analysis_type=aggregate`, `snapshot_id=null`, не подменяет source analyses.
- Prepared input содержит source identity и origin metadata для evidence. `AnalysisInput` в ADR-03 и `PreparedAnalysisInput` в §12 трактовать как единый pipeline boundary, не плодить два несвязанных контракта.
- В UI source status упомянут, но persisted status column в §8 нет: первоначально выводить состояние из данных/текущей операции либо отдельно согласовать расширение, не добавлять схему самовольно.

## 7. Risks

1. **Python 3.14 compatibility.** В отдельном Python 3.14.0 venv установка всех зависимостей текущего baseline `requirements.txt` и `python -m compileall -q backend desktop run.py` успешны. Подтверждённой несовместимости Python 3.14 сейчас нет. Ошибка импорта `Missing credentials` вызвана инициализацией AI client без ключа, а не версией Python или отсутствием зависимости. Установка и компиляция не доказывают полноценную runtime-совместимость: её всё ещё должны подтвердить тесты v2, включая новые PDF/browser зависимости и Windows lifecycle. Текущие нижние границы не фиксируют проверенный resolved set. PyMuPDF документирует Windows wheels и тестирование на 3.14 ([installation](https://pymupdf.readthedocs.io/en/latest/installation.html)); это не проверка данного окружения. Переход на другой Python не является исправлением F10 и сейчас не обоснован выявленной несовместимостью.
2. **Windows browser lifecycle.** Проверить subprocess/event loop, reload, остановку и очистку browser contexts на фактических версиях Uvicorn/Playwright. Python package не заменяет установку browser binaries ([Playwright setup](https://playwright.dev/python/docs/library)). Никаких runtime-download драйверов на HTTP request. Sandbox audit-сессии не равен пользовательскому запуску.
3. **Provider capability.** В v1 URL `https://api.proxyapi.ru/openai/v1`, в ТЗ — `https://api.proxyapi.ru/v1` и другие model IDs. Это осознанная смена настроек, не проверенная совместимость. Доступность заданной модели, vision + json_schema + strict + reasoning_effort и поведение параметров temperature/token limit необходимо подтвердить документацией провайдера и позднее отдельным разрешённым smoke. В этом аудите модели не вызывались, тариф/наличие модели не подтверждались.
4. **Strict schema не равна истинности.** Pydantic ловит структуру и score, но не доказывает evidence. Нужны source hints, ограничения размера контекста, prompt policy и тесты на отсутствие недоступных выводов. Материал сайта/документа — данные, не инструкции для приложения.
5. **SSRF и ресурсы.** Playwright выполняет много сетевых запросов; предварительный URL validator без контроля дальнейшей сети недостаточен. Browser/PDF/image concurrency и memory limits нужны до включения соответствующих endpoint. Не обещать абсолютную защиту от rebinding без проверки фактического соединения.
6. **SQLite и файлы.** Возможны lock contention, dangling paths и частичные операции. Не держать DB transaction при долгом I/O; проверить FK, rollback и deletion semantics. Полный source data может занимать много диска; политика очистки должна сохранять воспроизводимость snapshots.
7. **Legacy transition.** Смена глобального Settings затронет v1 endpoints и eager imports. Требуется временная совместимость настроек/ресурсов либо изоляция legacy маршрутов, а не скрытый запуск старых singleton при health. Не снимать selenium/openai legacy зависимости до развязки imports.
8. **Health vs incremental implementation.** §10 показывает `database:"ok"`, `browser:"ok"`, а план вводит DB на этапе 2 и browser на этапе 6. Нельзя на этапе 1 возвращать фиктивное `ok` или преждевременно реализовывать оба subsystem. Предложение и необходимое уточнение промежуточной семантики приведены в §9.
9. **PDF scope.** Локальная отрисовка помогает scanned PDF без отдельного OCR-сервиса, но не гарантирует качественное распознавание любой страницы. Ограничить DPI/пиксели/число страниц и сообщать limitations. Не добавлять Tesseract как обязательную зависимость без требования.

## 8. Verification baseline

Ниже сохранены результаты первоначального аудита и отдельно добавлены результаты последующего smoke test, предоставленные пользователем. При текущем обновлении документа smoke test не повторялся; установки зависимостей и проверки приложения заново не выполнялись.

### Первоначальный аудит

Команды выполнены из корня проекта. Для Git после первоначального отказа использован только command-scoped override: `git -c safe.directory=C:/Users/admin/CodexWorkspace/pem08-v2 ...`. Git config не изменялся.

| Реальная проверка | Результат |
|---|---|
| `Get-Location`, `rg --files --hidden -g '!.git/**'`, чтение файлов | Подтверждены структура, отсутствие локальных AGENTS/test/lock/CI и содержимое контрактов/кода |
| Первоначальные `git status`, `branch`, `log` без override | Отказ `detected dubious ownership`, владелец repo и sandbox identity различаются |
| `git -c safe.directory=... status --short`, `branch --show-current`, `log -1 --oneline`, `rev-parse HEAD`, `ls-files` | Чистый старт, develop-v2, baseline 4a0ebe1, 27 tracked-файлов |
| `python --version`, `Get-Command python,py,node` | Python 3.14.0: `C:\Users\admin\AppData\Local\Programs\Python\Python314\python.exe`; Node доступен |
| `py -0p` | `No installed Pythons found!`; launcher не нашёл регистрации в данном контексте, хотя `python` работает |
| Inline Python через `python -B -`: `compile(p.read_bytes(), str(p), 'exec')` для всех `*.py` | Успех: 14 файлов; компиляция в памяти без `.pyc` |
| `compile()` для `desktop/CompetitorMonitor.spec` | Синтаксис успешен; это не запуск сборки PyInstaller |
| Поиск `test_*.py`, `*_test.py`, pytest.ini/pyproject.toml/tox.ini/setup.cfg | Не найдено; тестового suite для запуска нет |
| `importlib.metadata.version` для dependency table | Только FastAPI/Uvicorn/Pydantic/requests установлены из внешних перечисленных пакетов; pytest отсутствует |
| `python -B -c "import backend.main"` | FAIL, exit 1: `ModuleNotFoundError: No module named 'pydantic_settings'` в `backend/config.py:7` |
| Inline `from backend.models.schemas import ...` | Успех; Pydantic schemas реально импортируются |
| Локальные assertions моделей | Пустые CompetitorAnalysis/ImageAnalysis принимаются; private URL принимается; текст 30001 символ принимается |
| JSON parse и `HistoryItem.model_validate` | Все 6 записей валидны; содержимое summaries не выводилось |
| AST import inventory | Подтверждены используемые и неиспользуемые прямые зависимости |
| `node --check frontend/app.js` | Успех, exit 0; browser rendering не проверяет |
| `git -c safe.directory=... check-ignore .env` | Exit 1, файл не игнорируется |

Воспроизводимое ядро выполненной проверки компиляции:

```powershell
@'
from pathlib import Path
files = sorted(Path.cwd().rglob('*.py'))
for p in files:
    compile(p.read_bytes(), str(p), 'exec')
print('COMPILE_OK', len(files))
compile(Path('desktop/CompetitorMonitor.spec').read_bytes(), 'CompetitorMonitor.spec', 'exec')
'@ | python -B -
```

В первоначальном аудите `pytest` не запускался: suite и пакет отсутствовали. Не запускались `run.py`, Selenium, desktop GUI/build/clean, AI endpoint и paid API. Не устанавливались зависимости или browser binaries. HTTP health приложения не проверен из-за import blocker. Не выполнялась browser UI проверка, нагрузочное тестирование, SSRF/XSS exploitation или полный dependency vulnerability scan. Успешная компиляция не равна успешному старту.

Историческая финальная проверка после первоначального создания документа:

- `git -c safe.directory=... diff --check` — exit 0, замечаний нет.
- `git -c safe.directory=... status --short` — только `?? docs/`.
- `git -c safe.directory=... status --short --untracked-files=all` — только `?? docs/V2_MIGRATION_PLAN.md`.
- `git -c safe.directory=... diff --exit-code HEAD` — exit 0, tracked-файлы не изменены, включая production-код и requirements.
- Отдельная Python-проверка untracked документа — UTF-8 читается, все 9 разделов присутствуют, code fences парные, trailing whitespace отсутствует, завершающий newline есть. Обычный `git diff --check` сам по себе untracked содержимое не проверяет.
- `__pycache__` не создан. Commit, staging, удалений и смены ветки не было.

### Последующий smoke test: отдельный Python 3.14 virtual environment

Источник: фактически выполненные проверки и результаты, сообщённые пользователем после установки baseline dependencies. Они относятся к отдельному venv, а не к неполному окружению первоначального аудита.

| Проверка | Фактический результат |
|---|---|
| Python | 3.14.0 |
| Установка всех зависимостей текущего корневого `requirements.txt` | Успешна на Python 3.14 |
| `python -m compileall -q backend desktop run.py` | Успешно |
| Git worktree после установки и проверок | Остался чистым |
| Импорт `backend.main` без API key | Ошибка `openai.OpenAIError: Missing credentials` |
| Причина ошибки импорта | При импорте `backend.services.openai_service` создаётся глобальный `openai_service = OpenAIService()`; конструктор сразу создаёт OpenAI client, SDK требует credentials |
| Локальный `/health` smoke test без API key | Заблокирован ошибкой импорта; успешный HTTP-ответ не получен |

Вывод: отсутствие зависимости больше не является причиной этого import failure; подтверждён архитектурный дефект F10. Ошибка не связана с Python 3.14. `/health` должен быть независим от AI credentials. Полная совместимость v2 остаётся предметом автоматических тестов; успешные dependency installation и compileall не подменяют runtime-проверку приложения.

## 9. Stage 1 proposal

Только предложение; ни один из пунктов ниже в этом аудите не реализован.

**Scope:** config, FastAPI lifespan, `/api/v2/health`, test foundation и необходимые для них запуск/директории/ignore/documentation. Без competitors CRUD, AI analysis, SQLite domain implementation, PDF, Playwright capture и UI redesign.

**Обязательная регрессия F10:** Stage 1 должен позволять импортировать FastAPI application без AI API key. Создание AI client не должно быть обязательным import-time side effect: использовать безопасную lazy initialization либо dependency-based initialization для AI-операций. Startup без ключа также не должен блокировать health. `/api/v2/health` должен возвращать ответ без обращения к AI provider, с `ai_configured:false` при отсутствии credentials.

Добавить автоматический тест в изолированном окружении без ключей и загрузки реальной `.env`: свежий import `backend.main` (без использования уже закешированного модуля), вход в lifespan и GET `/api/v2/health` завершаются успешно, HTTP status — 200, `ai_configured` — false. Spy/fake конструктора клиента и запрет внешнего I/O должны подтверждать, что на этом пути AI client не создаётся и provider не вызывается. Не подменять само FastAPI application или health handler: тест должен ловить реальную регрессию import-time initialization.

1. **Config.** Settings v2 с `APP_*`, `AI_*`, `DATABASE_URL`, upload/screenshot paths, лимитами и browser settings из §6. Пути разрешать относительно project root. Секрет хранить как secret value и не логировать. `AI_API_KEY` может отсутствовать для health/tests; `ai_configured` означает наличие настроек, не проверку работоспособности AI. Явно разобрать CSV `CORS_ORIGINS` из примера; не рассчитывать, что обычное list-поле Settings автоматически примет comma-separated значение. Добавить настраиваемый AI timeout, требуемый §17, с документированными единицами и default. `.env.example`, `.gitignore`, loopback default. Определить временное чтение старых PROXY_API_KEY/OPENAI_MODEL только для сохраняемых legacy routes, чтобы не смешивать v1/v2 base URL и model IDs.
2. **Lifespan.** Создать `backend/lifespan.py`; перенести startup/shutdown ответственность из decorators. Создание безопасных data dirs при startup, injectable resource factories/app state и гарантированный cleanup даже при частичном сбое. Не открывать сеть при import. На этом этапе не поднимать настоящий browser и не создавать schema competitors. Убрать eager legacy imports из health path; если v1 routes сохраняются, их ресурсы создавать лениво и закрывать явно. Проверять shutdown executor/client, а не только замену синтаксиса декоратора.
3. **Health.** Router `backend/api/health.py`, GET `/api/v2/health`, version `2.0.0`, поля `status`, `database`, `browser`, `ai_configured` сохраняются. Health не вызывает платный API и не открывает URL. До реализации нужно зафиксировать промежуточный контракт: предлагается HTTP 200 liveness, `status:"ok"`, `database:"not_initialized"`, `browser:"not_initialized"` на Foundation, а затем `ok` только после реальной инициализации. Это предложение допустимых промежуточных значений, отсутствующих в примере §10, требует явного решения при Stage 1; не выдавать его за утверждённую схему и не менять ТЗ молча. Финальный DoD требует работающие DB/browser. Если пример §10 должен выполняться буквально уже в Stage 1, отдельно пересмотреть scope этапа вместо фиктивного успеха.
4. **Test foundation.** Создать `tests/conftest.py`, `test_health.py`, `test_config.py`, `test_lifespan.py`. Использовать tmp_path и изолированное окружение без реальной `.env`; fake factories для ресурсов, запрет внешнего I/O. Проверить health без key, поля ответа, CORS allow/deny, config CSV/paths/invalid limits, отсутствие секрета в logs, repeated startup/shutdown и cleanup после partial failure. TestClient использовать как context manager для lifespan; ASGITransport сам по себе не считать доказательством прохождения lifespan. При async tests запускать lifecycle явно.
5. **Проверка Stage 1.** Изолированный venv и минимальный проверенный dependency набор; `python -m pytest -q`, compile/import, запуск `python run.py`, локальные GET `/api/v2/health`, `/`, `/openapi.json`; отсутствие AI key не блокирует health. Затем Git diff/check и точный отчёт. Установку и изменение requirements выполнять только в будущем этапе, не в аудите.

Критерий выхода: foundation действительно запускается и проходит offline tests, сообщает честное состояние ресурсов, не запускает legacy side effects при импорте, не содержит реализации этапов 2–8. Остальные файлы target map добавляются по мере появления их потребителей.
