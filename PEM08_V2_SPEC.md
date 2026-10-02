# PEM08 v2.0 — Техническое задание

**Проект:** Competitor Intelligence Assistant  
**Основа:** учебный проект `pem08` / «Мониторинг конкурентов»  
**Версия ТЗ:** 2.0  
**Статус:** implementation-ready  
**Целевая платформа:** локальное Web-приложение, Windows 11 / кроссплатформенный Python backend  
**Цель:** превратить исходный демонстрационный проект из набора независимых анализаторов в единое рабочее пространство конкурентного анализа.

---

## 1. Цель продукта

Пользователь создаёт карточки конкурентов, прикрепляет к ним источники разных типов — текст, изображение, PDF или URL — и получает структурированный, доказательный AI-анализ. Результаты сохраняются, могут повторно анализироваться и сравниваться между 2–5 конкурентами.

Проект должен демонстрировать полный мультимодальный pipeline:

`источник → нормализация → мультимодальный AI-анализ → строгая структура → хранение → визуализация → сравнение`.

Главный результат MVP — не «чат с моделью», а воспроизводимый аналитический workflow.

---

## 2. Scope MVP v2.0

### Обязательно

1. Карточки конкурентов: создать, открыть, изменить, удалить.
2. Источники конкурента:
   - текст;
   - изображение JPG/PNG/WebP;
   - PDF;
   - URL веб-страницы.
3. Единая схема AI-анализа для всех типов источников.
4. Доказательная база (`evidence`) для каждого существенного вывода.
5. Оценки по фиксированным измерениям 0–10 с текстовым обоснованием.
6. Сохранение конкурентов, источников и результатов в SQLite.
7. Повторный анализ существующего источника.
8. Для URL — повторный захват страницы и новый анализ.
9. Сравнение 2–5 конкурентов по общей матрице критериев.
10. Web UI как единственный обязательный интерфейс.
11. README, `.env.example`, тесты и Swagger/OpenAPI.

### Не входит в MVP

- PyQt6 и обязательная сборка `.exe`;
- Telegram-бот;
- авторизация и многопользовательский режим;
- облачный production deploy;
- Celery/RabbitMQ/Redis;
- векторная БД и RAG;
- автоматический scheduler мониторинга сайтов;
- обход CAPTCHA, Cloudflare или других антибот-систем;
- массовый crawling сайта;
- полноценный web-search агент;
- автоматическая публикация результатов;
- генерация изображений как обязательная функция.

Генерация улучшенного рекламного концепта остаётся **bonus-функцией после готовности ядра**.

---

## 3. Аудит исходного проекта v1

Исходный архив содержит:

```text
backend/
  main.py
  config.py
  models/schemas.py
  services/
    openai_service.py
    parser_service.py
    history_service.py
frontend/
  index.html
  styles.css
  app.js
desktop/
  main.py
  api_client.py
  styles.py
  build.py
requirements.txt
run.py
history.json
```

### Что сохраняем концептуально

- FastAPI как backend;
- простой браузерный Web UI;
- разделение API / services / schemas;
- мультимодальность text + image;
- URL-анализ;
- health endpoint;
- OpenAI-compatible API provider.

### Что заменяем

| v1 | v2 |
|---|---|
| `history.json` | SQLite |
| отдельные text/image JSON-схемы | единая `CompetitorAnalysis` |
| `OpenAI()` sync client внутри `async def` | `AsyncOpenAI` |
| regex-поиск JSON в ответе | strict JSON Schema / Pydantic validation |
| Selenium + webdriver-manager | async Playwright |
| `@app.on_event` | FastAPI lifespan |
| `allow_origins=["*"]` | CORS из config, localhost по умолчанию |
| независимые вкладки Text/Image/Parse | workspace конкурента |
| «История» как псевдопамять | версии источников и анализов |
| PyQt6-копия интерфейса | исключить из core MVP |
| permissive URL input | URL validation + SSRF protection |
| `innerHTML` для AI/user content | безопасный DOM rendering |
| обещанный, но отсутствующий PDF | реальный PDF pipeline |

---

## 4. Архитектурные решения

### ADR-01. Один backend, один Web UI

FastAPI обслуживает API и статический frontend. Отдельный desktop-клиент не поддерживается в core v2.0.

### ADR-02. SQLite — источник состояния

Файл БД: `data/app.db`.

Хранит:
- конкурентов;
- источники;
- snapshot источника;
- анализы;
- сравнения.

Файлы хранятся на диске в `data/uploads/`, БД содержит метаданные и относительные пути.

### ADR-03. Единый аналитический контракт

Текст, изображение, PDF и URL приводятся к единому `AnalysisInput` и возвращают один `CompetitorAnalysis`. UI не должен знать, какой промпт использовала модель.

### ADR-04. Структурированный вывод без regex

Основной runtime для ProxyAPI/OpenAI-compatible режима:

- `AsyncOpenAI`;
- multimodal Chat Completions;
- `response_format.type = json_schema`;
- `strict = true`;
- финальная валидация Pydantic.

Причина выбора Chat Completions как default transport: он явно документирован ProxyAPI одновременно для vision и strict structured output. Архитектура провайдера должна позволить позже добавить Responses API без изменения domain/service слоя.

### ADR-05. PDF обрабатывается локально

Использовать PyMuPDF (`fitz`):
- извлечь текст;
- получить метаданные и число страниц;
- отрендерить выбранные страницы в PNG/JPEG;
- передать AI текст + ограниченное число page images.

Это обеспечивает одинаковый pipeline при прямом OpenAI и ProxyAPI и не привязывает проект к provider-specific file upload.

### ADR-06. URL-анализ через Playwright

Playwright запускается один раз через FastAPI lifespan и переиспользует Chromium browser.

На один URL извлекаются:
- final URL после redirect;
- title;
- meta description;
- H1/H2;
- видимый текст страницы;
- viewport screenshot;
- опционально второй screenshot ниже первого экрана.

Не реализовывать stealth/anti-bot bypass.

### ADR-07. Evidence-first analysis

Любое существенное утверждение модели должно по возможности сопровождаться наблюдаемым основанием. Если данных недостаточно, модель обязана заполнить `limitations` вместо домысливания.

---

## 5. Целевой стек

### Backend

- Python 3.12+
- FastAPI
- Uvicorn
- Pydantic v2
- pydantic-settings
- OpenAI Python SDK (`AsyncOpenAI`)
- SQLAlchemy 2.x
- SQLite
- Playwright async
- PyMuPDF
- Pillow
- python-multipart
- httpx

### Testing

- pytest
- pytest-asyncio
- FastAPI TestClient / httpx ASGITransport

### Frontend

Для учебного MVP оставить без build-step:

- HTML5
- CSS
- Vanilla JavaScript ES modules

Не добавлять React/Vue только ради внешнего вида.

---

## 6. Конфигурация `.env`

```env
APP_ENV=development
APP_HOST=127.0.0.1
APP_PORT=8000

AI_PROVIDER=proxyapi
AI_API_KEY=
AI_BASE_URL=https://api.proxyapi.ru/v1
AI_MODEL=openai/gpt-6-luna
AI_REASONING_EFFORT=low

# Для прямого OpenAI:
# AI_PROVIDER=openai
# AI_BASE_URL=https://api.openai.com/v1
# AI_MODEL=gpt-6-luna

DATABASE_URL=sqlite:///./data/app.db
UPLOAD_DIR=./data/uploads
SCREENSHOT_DIR=./data/screenshots

MAX_IMAGE_MB=10
MAX_PDF_MB=25
MAX_TEXT_CHARS=30000
MAX_WEB_TEXT_CHARS=25000
MAX_PDF_PAGES_ANALYZED=8

BROWSER_HEADLESS=true
BROWSER_TIMEOUT_MS=20000
BROWSER_VIEWPORT_WIDTH=1440
BROWSER_VIEWPORT_HEIGHT=1200

CORS_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
LOG_LEVEL=INFO
```

### Модель по умолчанию

Для учебного MVP использовать экономичную мультимодальную модель уровня `gpt-6-luna` / `openai/gpt-6-luna`.

Отдельный режим `AI_MODEL_DEEP` можно добавить позже для итогового cross-competitor анализа, но это не обязательное требование v2.0.

---

## 7. Целевая структура каталогов

```text
pem08-v2/
├── backend/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── lifespan.py
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── health.py
│   │   ├── competitors.py
│   │   ├── sources.py
│   │   ├── analyses.py
│   │   └── comparisons.py
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── api.py
│   │   ├── analysis.py
│   │   └── db.py
│   │
│   ├── repositories/
│   │   ├── competitors.py
│   │   ├── sources.py
│   │   ├── analyses.py
│   │   └── comparisons.py
│   │
│   ├── services/
│   │   ├── ai_service.py
│   │   ├── prompt_service.py
│   │   ├── browser_service.py
│   │   ├── document_service.py
│   │   ├── ingestion_service.py
│   │   ├── analysis_service.py
│   │   ├── comparison_service.py
│   │   └── storage_service.py
│   │
│   ├── security/
│   │   └── url_validation.py
│   │
│   └── db/
│       ├── session.py
│       └── init_db.py
│
├── frontend/
│   ├── index.html
│   ├── styles.css
│   ├── app.js
│   ├── api.js
│   ├── state.js
│   └── render.js
│
├── data/
│   ├── .gitkeep
│   ├── uploads/
│   └── screenshots/
│
├── tests/
│   ├── conftest.py
│   ├── test_health.py
│   ├── test_competitors.py
│   ├── test_sources.py
│   ├── test_analysis_schemas.py
│   ├── test_url_validation.py
│   └── test_ai_service_mocked.py
│
├── .env.example
├── .gitignore
├── requirements.txt
├── run.py
├── README.md
└── AGENTS.md
```

---

## 8. Модель данных SQLite

### `competitors`

| поле | тип | правило |
|---|---|---|
| id | UUID/string | PK |
| name | text | required, 1–120 |
| website_url | text nullable | optional |
| niche | text nullable | optional |
| notes | text nullable | optional |
| created_at | datetime UTC | required |
| updated_at | datetime UTC | required |

### `sources`

| поле | тип | правило |
|---|---|---|
| id | UUID/string | PK |
| competitor_id | FK | required |
| source_type | enum | text/image/pdf/url |
| label | text | required |
| original_filename | text nullable | file sources |
| mime_type | text nullable | file sources |
| url | text nullable | URL source |
| storage_path | text nullable | uploaded file |
| sha256 | text nullable | uploaded file |
| created_at | datetime UTC | required |

### `source_snapshots`

Snapshot — фактический контент, который анализировался. Для text/image/PDF обычно создаётся один snapshot, для URL — новый при каждом refresh.

| поле | тип |
|---|---|
| id | UUID/string |
| source_id | FK |
| captured_at | datetime UTC |
| final_url | text nullable |
| title | text nullable |
| meta_description | text nullable |
| extracted_text | text nullable |
| screenshot_path | text nullable |
| secondary_screenshot_path | text nullable |
| metadata_json | JSON/text |

### `analyses`

| поле | тип |
|---|---|
| id | UUID/string |
| competitor_id | FK |
| snapshot_id | FK nullable |
| analysis_type | enum: source / aggregate |
| model_id | text |
| prompt_version | text |
| result_json | JSON/text |
| input_tokens | integer nullable |
| output_tokens | integer nullable |
| duration_ms | integer nullable |
| created_at | datetime UTC |

### `comparisons`

| поле | тип |
|---|---|
| id | UUID/string |
| competitor_ids_json | JSON/text |
| model_id | text |
| result_json | JSON/text |
| created_at | datetime UTC |

---

## 9. Pydantic domain schemas

Ниже — обязательный логический контракт. Coding agent может разбить его по файлам, но не должен менять имена/смысл полей без причины.

```python
from enum import StrEnum
from pydantic import BaseModel, Field, HttpUrl


class SourceType(StrEnum):
    text = "text"
    image = "image"
    pdf = "pdf"
    url = "url"


class ConfidenceLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class ScoreItem(BaseModel):
    score: int = Field(ge=0, le=10)
    rationale: str = Field(min_length=3, max_length=700)


class AnalysisScorecard(BaseModel):
    positioning_clarity: ScoreItem
    value_proposition: ScoreItem
    trust: ScoreItem
    cta_strength: ScoreItem
    visual_consistency: ScoreItem | None = None
    ux_clarity: ScoreItem | None = None


class EvidenceItem(BaseModel):
    category: str = Field(min_length=2, max_length=80)
    finding: str = Field(min_length=3, max_length=700)
    evidence: str = Field(min_length=1, max_length=1200)
    source_hint: str = Field(min_length=1, max_length=300)
    confidence: ConfidenceLevel


class CompetitorAnalysis(BaseModel):
    executive_summary: str = Field(min_length=10, max_length=2500)
    positioning: str = Field(min_length=3, max_length=1500)
    target_audience: list[str] = Field(default_factory=list, max_length=8)
    value_propositions: list[str] = Field(default_factory=list, max_length=8)
    differentiators: list[str] = Field(default_factory=list, max_length=8)
    strengths: list[str] = Field(default_factory=list, max_length=8)
    gaps: list[str] = Field(default_factory=list, max_length=8)
    marketing_messages: list[str] = Field(default_factory=list, max_length=8)
    scorecard: AnalysisScorecard
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=20)
    opportunities: list[str] = Field(default_factory=list, max_length=8)
    recommended_actions: list[str] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=8)
```

### API request schemas

```python
class CompetitorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    website_url: HttpUrl | None = None
    niche: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=2000)


class CompetitorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    website_url: HttpUrl | None = None
    niche: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=2000)


class TextSourceCreate(BaseModel):
    label: str = Field(default="Текст", max_length=120)
    text: str = Field(min_length=10, max_length=30000)


class UrlSourceCreate(BaseModel):
    label: str = Field(default="Сайт", max_length=120)
    url: HttpUrl


class ComparisonRequest(BaseModel):
    competitor_ids: list[str] = Field(min_length=2, max_length=5)
```

### Сравнение

```python
class CompetitorComparisonRow(BaseModel):
    competitor_id: str
    competitor_name: str
    positioning: str
    strengths: list[str]
    gaps: list[str]
    positioning_clarity: int = Field(ge=0, le=10)
    value_proposition: int = Field(ge=0, le=10)
    trust: int = Field(ge=0, le=10)
    cta_strength: int = Field(ge=0, le=10)


class ComparisonResult(BaseModel):
    executive_summary: str
    competitors: list[CompetitorComparisonRow]
    shared_patterns: list[str]
    meaningful_differences: list[str]
    market_gaps: list[str]
    opportunities: list[str]
    limitations: list[str]
```

---

## 10. API v2

Все API endpoint начинаются с `/api/v2`.

### Health

`GET /api/v2/health`

Response:

```json
{
  "status": "ok",
  "version": "2.0.0",
  "database": "ok",
  "browser": "ok",
  "ai_configured": true
}
```

### Competitors

| method | endpoint | назначение |
|---|---|---|
| POST | `/api/v2/competitors` | создать конкурента |
| GET | `/api/v2/competitors` | список |
| GET | `/api/v2/competitors/{id}` | карточка + источники + последний анализ |
| PATCH | `/api/v2/competitors/{id}` | изменить |
| DELETE | `/api/v2/competitors/{id}` | удалить |

### Sources

| method | endpoint | назначение |
|---|---|---|
| POST | `/api/v2/competitors/{id}/sources/text` | добавить текст и автоматически проанализировать |
| POST | `/api/v2/competitors/{id}/sources/file` | загрузить image/PDF и автоматически проанализировать |
| POST | `/api/v2/competitors/{id}/sources/url` | открыть URL, создать snapshot и проанализировать |
| GET | `/api/v2/sources/{source_id}` | получить source + snapshots |
| DELETE | `/api/v2/sources/{source_id}` | удалить source |
| POST | `/api/v2/sources/{source_id}/reanalyze` | повторить AI-анализ текущего snapshot |
| POST | `/api/v2/sources/{source_id}/refresh` | только URL: новый browser snapshot + анализ |

### Analyses

| method | endpoint | назначение |
|---|---|---|
| GET | `/api/v2/competitors/{id}/analyses` | история результатов |
| GET | `/api/v2/analyses/{analysis_id}` | конкретный результат |
| POST | `/api/v2/competitors/{id}/aggregate-analysis` | сводный анализ всех активных источников |

### Compare

`POST /api/v2/comparisons`

```json
{
  "competitor_ids": ["id-1", "id-2", "id-3"]
}
```

Возвращает `ComparisonResult`.

---

## 11. Pipeline по типам источников

### 11.1 Text

```text
TextSourceCreate
  → validate length
  → save source
  → create snapshot(extracted_text)
  → analysis_service
  → AI structured output
  → save analysis
  → response
```

### 11.2 Image

Принимать только:
- `image/jpeg`;
- `image/png`;
- `image/webp`.

Не принимать GIF в v2.

Проверки:
1. MIME type;
2. magic bytes / Pillow verify;
3. размер `<= MAX_IMAGE_MB`;
4. SHA-256;
5. безопасное имя файла генерируется приложением, исходное имя хранится только как metadata.

Pipeline:

```text
upload
 → validate
 → save file
 → create thumbnail/metadata
 → multimodal AI(text instruction + image)
 → CompetitorAnalysis
 → save
```

### 11.3 PDF

Проверки:
- MIME `application/pdf`;
- signature `%PDF`;
- размер `<= MAX_PDF_MB`.

`document_service.py` должен:
1. открыть PDF через PyMuPDF;
2. определить page count;
3. извлечь текст;
4. выбрать максимум `MAX_PDF_PAGES_ANALYZED` страниц;
5. обязательно включить первую страницу;
6. если документ длинный — равномерно выбрать дополнительные страницы;
7. отрендерить их с разумным DPI;
8. собрать текст с лимитом;
9. передать текст + изображения страниц AI.

В `limitations` указать, если анализировалась не каждая страница.

### 11.4 URL

`url_validation.py` выполняется **до Playwright**.

Разрешено:
- только `http` и `https`;
- публичные IP.

Запрещено:
- `localhost`;
- `.local`;
- `127.0.0.0/8`;
- `10.0.0.0/8`;
- `172.16.0.0/12`;
- `192.168.0.0/16`;
- link-local `169.254.0.0/16`;
- IPv6 loopback/link-local/private;
- `file://`, `ftp://`, `data:` и другие схемы.

После DNS resolve проверить полученные IP повторно.

Playwright flow:

```text
validate URL
 → new browser context
 → goto(wait_until="domcontentloaded")
 → wait for body / short settle delay
 → resolve final URL
 → title
 → meta description
 → H1/H2
 → visible body text
 → screenshot 1440x1200
 → optional second screenshot after scroll
 → close context
 → store snapshot
 → AI analysis
```

Не использовать:
- stealth plugins;
- CAPTCHA solvers;
- fingerprint spoofing;
- обход paywall/auth.

При блокировке сайта вернуть контролируемый статус и предложить пользователю загрузить screenshot вручную.

---

## 12. AI service contract

`analysis_service` не должен знать детали HTTP API провайдера.

Интерфейс:

```python
class AIService:
    async def analyze_source(self, prepared_input: PreparedAnalysisInput) -> CompetitorAnalysis:
        ...

    async def compare_competitors(self, payload: PreparedComparisonInput) -> ComparisonResult:
        ...
```

`PreparedAnalysisInput` содержит:
- competitor_name;
- source_type;
- source_label;
- text context;
- 0..N image inputs;
- origin metadata.

### System prompt policy

Промпт обязан требовать:

1. анализировать только предоставленный материал;
2. отделять наблюдение от интерпретации;
3. не выдумывать свойства бизнеса, которых нет во входных данных;
4. добавлять `evidence`;
5. снижать `confidence`, если вывод косвенный;
6. писать ограничения в `limitations`;
7. все score 0–10 сопровождать `rationale`;
8. отвечать на русском;
9. выдавать только schema-compliant result.

### Prompt versioning

Ввести константу:

```python
ANALYSIS_PROMPT_VERSION = "competitor-analysis-v2.0"
COMPARISON_PROMPT_VERSION = "competitor-comparison-v1.0"
```

Версия сохраняется в `analyses.prompt_version`.

---

## 13. Ошибки API

Не возвращать `200 {success:false}` для ошибок.

Использовать HTTP status:

- 400 — некорректный файл/URL;
- 404 — competitor/source/analysis не найден;
- 413 — файл превышает лимит;
- 422 — Pydantic validation;
- 502 — AI provider error;
- 504 — browser/provider timeout;
- 500 — непредвиденная внутренняя ошибка.

Единый error response:

```json
{
  "error": {
    "code": "URL_BLOCKED_PRIVATE_NETWORK",
    "message": "Этот адрес нельзя анализировать.",
    "details": null
  }
}
```

В UI показывать безопасное пользовательское сообщение; stack trace — только в server log.

---

## 14. Web UI v2

### Общая концепция

Не делать интерфейс как четыре независимые вкладки API.

Главный экран — аналитическое рабочее пространство.

Desktop layout `>= 1200px`:

```text
┌───────────────┬──────────────────────────┬──────────────────────────────────────┐
│ Competitors   │ Sources                  │ Analysis                             │
│               │                          │                                      │
│ + Add         │ + Text                   │ Executive summary                    │
│ Alpha         │ + Image/PDF              │ Scorecard                            │
│ Beta          │ + URL                    │                                      │
│ Gamma         │                          │ Overview / Evidence / Actions         │
│               │ selected source preview  │ / Limitations                        │
└───────────────┴──────────────────────────┴──────────────────────────────────────┘
```

### Левая колонка — Competitors

Ширина ~240–280 px.

Содержит:
- название продукта `Competitor Intelligence`;
- кнопку `+ Конкурент`;
- список карточек;
- имя;
- домен (если есть);
- количество источников;
- дата последнего анализа.

Активный конкурент визуально выделяется.

### Центральная колонка — Sources

Ширина ~340–420 px.

Header:
- имя конкурента;
- `Добавить источник`.

Кнопки:
- `Текст`;
- `Изображение / PDF`;
- `URL`.

Карточка source:
- type icon;
- label;
- filename/domain;
- timestamp;
- status;
- actions `Повторить анализ`, `Обновить` (URL), `Удалить`.

Для image показывать preview.
Для PDF — имя, page count и размер.
Для URL — screenshot preview.

### Правая колонка — Analysis

Header actions:
- `Сводный анализ`;
- `Сравнить`;
- `Экспорт Markdown` — bonus, если успеваем.

Секция Summary:
- executive summary;
- positioning;
- target audience;
- value propositions.

Scorecards:
- Positioning clarity;
- Value proposition;
- Trust;
- CTA;
- Visual consistency;
- UX clarity.

Каждый score показывает:
- число `/10`;
- progress bar;
- rationale.

Tabs:
1. `Overview`
2. `Evidence`
3. `Actions`
4. `Limitations`

### Compare mode

Отдельный overlay/page.

Пользователь выбирает 2–5 конкурентов.

Показывать:
- таблицу одинаковых scores;
- позиционирование;
- сильные стороны;
- gaps;
- shared patterns;
- meaningful differences;
- market gaps;
- opportunities.

**Не объявлять автоматически «победителя».** Сравнение должно показывать измерения и различия.

---

## 15. Визуальная система

Цель — «B2B intelligence dashboard», а не neon AI demo.

### Theme

- фон: глубокий графит / slate;
- панели: слегка светлее фона;
- основной accent: холодный blue/cyan, но без яркого glow;
- success/warning/error — стандартные семантические состояния;
- radius 10–14 px;
- subtle borders вместо тяжёлых теней;
- плотность интерфейса средняя.

### Typography

Один основной sans-serif stack, без обязательной загрузки Google Fonts:

```css
font-family: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
```

### Loading UX

Убрать полноэкранный overlay, который блокирует всё приложение.

Вместо него отображать status card:

```text
✓ Источник принят
✓ Контент подготовлен
● Выполняется AI-анализ
○ Сохраняется отчёт
```

Для URL:

```text
✓ URL проверен
✓ Страница загружена
✓ Screenshot создан
● Анализируется
```

Если backend пока синхронный, UI может отображать известные локальные стадии до ответа; после v2.0 можно перейти на job status/SSE.

### Empty states

Нужны отдельные empty state:
- нет конкурентов;
- у конкурента нет sources;
- source ещё не анализировался;
- сравнение ещё не выбрано.

---

## 16. Frontend security

Запрещено вставлять строки пользователя/AI напрямую через template string + `innerHTML`.

Для динамических данных использовать:
- `textContent`;
- `createElement`;
- helper-функции безопасного DOM rendering.

`innerHTML` допустим только для статических заранее определённых SVG/template фрагментов без пользовательских данных.

---

## 17. Backend security / robustness

Обязательно:

1. `.env` в `.gitignore`.
2. API key никогда не логируется целиком.
3. upload limits контролируются backend, не только UI.
4. MIME проверяется фактическим чтением файла.
5. filenames нормализуются приложением.
6. CORS — только configured origins.
7. SSRF protection для URL.
8. browser context закрывается после каждого capture.
9. глобальный browser закрывается в lifespan.
10. exception details не выдаются пользователю как raw `str(e)` для внутренних ошибок.
11. AI timeout и browser timeout задаются config.
12. не логировать целиком большие тексты, изображения/base64 или PDF content.

---

## 18. Lifespan

В `lifespan.py`:

```text
startup:
  create data dirs
  init database
  create AI client
  start Playwright
  launch Chromium

yield

shutdown:
  close Chromium
  stop Playwright
  close AI/http clients if required
```

`@app.on_event("startup")` и `@app.on_event("shutdown")` не использовать.

---

## 19. Logging

Структурированные понятные сообщения без emoji-зависимости.

Для каждого анализа генерировать `request_id`/`operation_id`.

Минимально логировать:
- operation id;
- endpoint;
- source type;
- competitor id;
- duration;
- model;
- token usage при наличии;
- success/failure code.

Не логировать:
- API secret;
- base64;
- полный пользовательский документ;
- полный AI response при обычном INFO.

---

## 20. Tests — обязательный минимум

До сдачи должны проходить все тесты командой:

```powershell
pytest -q
```

### Unit

1. `CompetitorAnalysis` не принимает score > 10.
2. `ComparisonRequest` требует 2–5 id.
3. private IPv4 URL блокируется.
4. localhost блокируется.
5. public https URL проходит validation.
6. image MIME/size validation.
7. PDF signature/size validation.
8. AI structured response проходит Pydantic validation.
9. invalid AI payload превращается в controlled provider error.

### API

1. `/api/v2/health` → 200.
2. create/list/get/update/delete competitor.
3. add text source с mocked AI.
4. invalid upload → 400/413.
5. unknown competitor → 404.
6. compare с одним competitor → 422.

### Browser service

Не запускать внешний сайт в обычном unit test suite.

Отдельный optional integration test разрешён для локальной HTML fixture.

---

## 21. Definition of Done v2.0

Проект считается готовым, только если выполнено всё ниже:

- [ ] приложение запускается одной командой `python run.py`;
- [ ] `/api/v2/health` = 200;
- [ ] UI открывается на localhost;
- [ ] можно создать минимум 3 конкурентов;
- [ ] text source анализируется и сохраняется;
- [ ] image source анализируется и сохраняется;
- [ ] PDF source реально анализируется;
- [ ] URL открывается Playwright и анализируется по text + screenshot;
- [ ] URL private network блокируется;
- [ ] результат соответствует `CompetitorAnalysis`;
- [ ] evidence отображается в UI;
- [ ] score rationale отображается в UI;
- [ ] reanalyze создаёт новую запись анализа;
- [ ] URL refresh создаёт новый snapshot;
- [ ] aggregate-analysis работает;
- [ ] compare 2–5 competitors работает;
- [ ] нет regex JSON parsing;
- [ ] нет `webdriver-manager`;
- [ ] нет `history.json` как runtime storage;
- [ ] нет raw user/AI data через `innerHTML`;
- [ ] PyQt6 не нужен для запуска core проекта;
- [ ] `.env` не попадает в Git;
- [ ] `pytest -q` проходит;
- [ ] README соответствует реальному функционалу.

---

## 22. Порядок реализации

### Этап 0 — безопасная точка старта

1. Создать новую ветку `feature/pem08-v2` или новую директорию проекта.
2. Не менять исходный архив.
3. Зафиксировать текущий baseline.
4. Создать `.env.example` без секретов.

**Acceptance:** исходник сохранён, рабочая ветка чистая.

### Этап 1 — Foundation

Создать:
- новый config;
- lifespan;
- `/api/v2/health`;
- новую структуру modules;
- pytest setup;
- data directories.

Удалять v1 endpoints пока не обязательно.

**Acceptance:** приложение стартует, health/test работают.

### Этап 2 — SQLite + competitors

Реализовать DB models, repository и CRUD.

**Acceptance:** создать/получить/обновить/удалить competitor через Swagger и tests.

### Этап 3 — новый AI service

1. `AsyncOpenAI`.
2. strict JSON schema.
3. Pydantic validation.
4. единый `CompetitorAnalysis`.
5. mocked tests.

На этом этапе **не подключать Playwright/PDF**.

**Acceptance:** text analysis стабильно возвращает валидную schema без regex.

### Этап 4 — Sources: text + image

1. storage service;
2. source/snapshot tables;
3. text endpoint;
4. image validation/upload;
5. image multimodal analysis;
6. persistence.

**Acceptance:** оба source type отображаются в API и UI.

### Этап 5 — PDF

1. PyMuPDF;
2. extraction;
3. selected-page rendering;
4. multimodal analysis;
5. limitations.

**Acceptance:** реальный PDF даёт сохранённый структурированный анализ.

### Этап 6 — URL + Playwright

1. SSRF validator;
2. Playwright browser lifecycle;
3. DOM/text extraction;
4. screenshots;
5. source snapshot;
6. refresh.

**Acceptance:** публичный URL анализируется; localhost/private IP блокируется.

### Этап 7 — aggregate + compare

1. aggregate source analyses into competitor profile;
2. ComparisonResult;
3. compare endpoint.

**Acceptance:** 2–5 competitors формируют comparison.

### Этап 8 — UI redesign

Порядок:
1. competitors pane;
2. sources pane;
3. analysis pane;
4. add source dialogs;
5. scorecards;
6. evidence/actions/limitations tabs;
7. compare UI;
8. responsive state.

**Acceptance:** функционал v2 доступен без Swagger.

### Этап 9 — hardening

1. upload limits;
2. errors;
3. CORS;
4. safe DOM;
5. logs;
6. test coverage;
7. README;
8. удалить мёртвый v1 code.

**Acceptance:** полный Definition of Done.

---

## 23. Что удалить/архивировать после миграции

После полного прохождения DoD:

- удалить `backend/services/history_service.py`;
- удалить runtime `history.json`;
- удалить старый Selenium `parser_service.py`;
- удалить `_parse_json_response()`;
- удалить старые `/analyze_text`, `/analyze_image`, `/parse_demo` или оставить временные 301/compat routes только если это требуется преподавателем;
- перенести `desktop/` в `legacy/desktop-v1/` либо убрать из учебного репозитория;
- обновить README и docs так, чтобы они не утверждали наличие функций, которых нет.

---

## 24. Bonus после v2.0

Только после полного DoD:

### A. Visual concept generation

Кнопка:

`Создать улучшенный концепт`

Input:
- исходный screenshot/image;
- `CompetitorAnalysis`;
- selected recommendations.

Output:
- краткий creative brief;
- новое изображение через image model;
- side-by-side preview.

### B. Change detection

Для URL сравнивать текущий snapshot с предыдущим:
- headline changes;
- CTA changes;
- new/removed messages;
- visual changes.

### C. Export

Markdown/HTML/PDF отчёт по одному competitor или comparison.

---

## 25. Ограничения для coding agent

Coding agent должен соблюдать правила:

1. Не переписывать весь проект одним большим коммитом без промежуточной проверки.
2. После каждого этапа запускать соответствующие tests.
3. Не добавлять библиотеки без необходимости.
4. Не добавлять React, Docker, Redis, Celery, LangChain или vector DB без отдельного решения пользователя.
5. Не менять public API v2, Pydantic contracts и DB semantics самовольно.
6. Не хранить secret в репозитории.
7. Не скрывать ошибки fallback-логикой: ошибка должна быть диагностируема.
8. Не имитировать успешный AI response при реальном provider failure.
9. Не обходить anti-bot/captcha.
10. Перед удалением v1 кода убедиться, что v2 test suite зелёный.
11. После UI изменений вручную проверить localhost в браузере: console errors, network errors, loading states и responsive layout.
12. В конце каждого этапа выдавать:
    - изменённые файлы;
    - что реализовано;
    - команды проверки;
    - результат tests;
    - известные ограничения;
    - следующий этап.

---

## 26. Первый prompt для Cursor/Codex

```text
Ты работаешь над учебным проектом PEM08 Competitor Intelligence Assistant v2.0.

Источник требований: PEM08_V2_SPEC.md в корне проекта. Этот файл является главным контрактом реализации.

Задача сейчас: выполнить только Этап 0 и Этап 1 из раздела «Порядок реализации».

Правила:
- сначала изучи текущую структуру проекта и PEM08_V2_SPEC.md;
- не реализуй последующие этапы заранее;
- сохрани работоспособность исходного проекта, если это не мешает foundation v2;
- используй Python 3.12+, FastAPI, Pydantic v2;
- настрой lifespan вместо deprecated startup/shutdown events для v2;
- создай /api/v2/health;
- подготовь новую модульную структуру;
- создай data directories безопасно;
- настрой pytest;
- секреты не добавляй;
- не добавляй React/Docker/Redis/Celery/LangChain;
- после изменений запусти тесты и старт приложения;
- не заявляй успех без фактической проверки.

В конце выведи:
1. список изменённых файлов;
2. что реализовано;
3. команды проверки;
4. фактический результат тестов;
5. что осталось до Этапа 2.
```

---

## 27. Итоговая продуктовая формула v2.0

```text
Competitor
  ├─ Text source
  ├─ Image source
  ├─ PDF source
  └─ URL source → Playwright snapshot
           ↓
      multimodal preparation
           ↓
       AI analysis
      strict schema
           ↓
        evidence
       + scorecards
       + actions
       + limitations
           ↓
          SQLite
           ↓
   Workspace / Compare UI
```

Это целевой scope. Всё, что не помогает этому pipeline, считается вторичным до завершения Definition of Done.
