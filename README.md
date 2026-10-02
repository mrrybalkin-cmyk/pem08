# 🔍 Мониторинг конкурентов - AI Ассистент

## v2 — Stage 1 Foundation

Stage 1 добавляет конфигурацию, lifespan и `GET /api/v2/health`.
SQLite, Playwright, PDF, CRUD и AI pipeline v2 пока не реализованы.
Существующий UI и v1 API сохранены; описание ниже относится к v1.

Команды Windows/PowerShell выполняются из корня проекта с активированным
virtual environment Python 3.14 (либо используйте собственный путь к его Python):

```powershell
python run.py
```

Ключ для запуска и health не нужен. Шаблон конфигурации — `.env.example`;
локальный `.env` игнорируется Git. Относительные пути upload/screenshot/history
разрешаются от корня проекта. По умолчанию сервер слушает `127.0.0.1:8000`,
CORS разрешает только origins из `CORS_ORIGINS` (CSV).

Health возвращает HTTP 200:

```json
{"status":"ok","version":"2.0.0","database":"not_initialized","browser":"not_initialized","ai_configured":false}
```

`ai_configured` отражает только наличие непустого `AI_API_KEY` для v2,
а не доступность провайдера. `AI_*` подготовлены по ТЗ для будущего pipeline.
Сохранённые v1 AI endpoints используют отдельные `PROXY_API_KEY`,
`PROXY_API_BASE_URL`, `OPENAI_MODEL`, `OPENAI_VISION_MODEL`; v2 URL/model IDs
не подставляются в legacy запросы. Без legacy ключа возвращается явная ошибка
в существующем v1 формате `success:false`. `AI_TIMEOUT_SECONDS` (default 60 секунд)
применяется к legacy клиенту. `API_HOST/API_PORT` поддерживаются как старые aliases,
приоритет имеют `APP_HOST/APP_PORT`.

Lifespan создаёт каталоги uploads/screenshots без сетевых запросов. AI client и
Selenium executor создаются лениво, закрываются при shutdown и могут создаваться
снова при следующем запуске. История не читается и не создаётся при импорте.

Offline-проверки:

```powershell
python -m compileall -q backend desktop run.py
python -m pytest -q
python -c "import backend.main; print('IMPORT_OK')"
```

Тесты изолируют environment и `.env`, используют временные каталоги,
блокируют сетевой I/O (кроме внутреннего Windows socketpair для asyncio),
подменяют AI SDK и не запускают браузер. Для установки тестовых зависимостей:

```powershell
python -m pip install -r requirements-dev.txt
```

`requirements.txt` содержит только runtime dependencies;
новое окружение внутри репозитория не требуется.

Промежуточный контракт Stage 1 уточняет пример полного v2 из §10/§18 ТЗ:
database/browser честно `not_initialized`, AI client не создаётся на startup.
Это соответствует ограничению Stage 1; архитектурные документы не переписаны.

MVP приложение для анализа конкурентной среды с поддержкой мультимодальности (текст и изображения).

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)
![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4o-purple.svg)

## 📋 Описание

Приложение позволяет:
- **Анализировать текст конкурентов** — получать структурированную аналитику с сильными/слабыми сторонами, уникальными предложениями и рекомендациями
- **Анализировать изображения** — баннеры, скриншоты сайтов, упаковки товаров с оценкой визуального стиля
- **Парсить сайты** — автоматически извлекать и анализировать контент по URL
- **Хранить историю** — последние 10 запросов сохраняются для быстрого доступа

## 🚀 Быстрый старт

### 1. Клонирование и установка зависимостей

```bash
# Клонируйте репозиторий
cd competitor-monitor

# Создайте виртуальное окружение
python -m venv venv

# Активируйте окружение
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Установите зависимости
pip install -r requirements.txt
```

### 2. Настройка переменных окружения

Создайте файл `.env` в корне проекта (используйте `.env.example` как шаблон):

```env
PROXY_API_KEY=your_proxy_api_key_here
OPENAI_MODEL=gpt-4o-mini
OPENAI_VISION_MODEL=gpt-4o-mini
```

### 3. Запуск приложения

```bash
# Запуск сервера
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Приложение будет доступно по адресу: http://localhost:8000

## 📁 Структура проекта

```
competitor-monitor/
├── backend/
│   ├── __init__.py
│   ├── main.py              # FastAPI приложение
│   ├── config.py            # Конфигурация
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py       # Pydantic модели
│   └── services/
│       ├── __init__.py
│       ├── openai_service.py    # Работа с OpenAI API
│       ├── parser_service.py    # Парсинг веб-страниц
│       └── history_service.py   # Управление историей
├── frontend/
│   ├── index.html           # HTML страница
│   ├── styles.css           # Стили
│   └── app.js               # JavaScript логика
├── requirements.txt         # Runtime dependencies
├── requirements-dev.txt     # Runtime и test dependencies
├── .env.example             # Пример .env файла
├── history.json             # Файл истории (создаётся автоматически)
├── README.md                # Этот файл
└── docs.md                  # Документация API
```

## 🔧 Функциональность

### Анализ текста (`/analyze_text`)
- Принимает текст конкурента (минимум 10 символов)
- Возвращает:
  - Сильные стороны
  - Слабые стороны
  - Уникальные предложения
  - Рекомендации по улучшению
  - Общее резюме

### Анализ изображений (`/analyze_image`)
- Принимает изображения: PNG, JPG, GIF, WEBP
- Возвращает:
  - Описание изображения
  - Маркетинговые инсайты
  - Оценку визуального стиля (0-10)
  - Рекомендации

### Парсинг сайтов (`/parse_demo`)
- Принимает URL сайта
- Извлекает: title, h1, первый абзац
- Автоматически анализирует извлечённый контент

### История (`/history`)
- Хранит последние 10 запросов
- Сохраняет тип запроса, краткое описание, время

## 🛠️ Технологии

- **Backend**: FastAPI, Python 3.9+
- **AI**: OpenAI GPT-4o-mini (или GPT-4.1)
- **Frontend**: Vanilla JS, CSS3
- **Парсинг**: BeautifulSoup4, httpx
- **Валидация**: Pydantic

## 📖 API Документация

После запуска сервера доступна интерактивная документация:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

Подробная документация API в файле [docs.md](docs.md)

## ⚠️ Требования

- Python 3.9+
- OpenAI API ключ с доступом к GPT-4o-mini или GPT-4.1
- Интернет-соединение для работы AI и парсинга

## 📝 Лицензия

MIT License

