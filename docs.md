# V2 API / Web quick reference

Актуальные установка, конфигурация, ограничения и команды проверки — в [README](README.md).
OpenAPI: `/openapi.json`; интерактивная документация `/docs`, `/redoc`.
Основной UI `/`, ES modules `/static/js/`, CSS `/static/workspace.css`.

| Method | Route | Результат |
|---|---|---|
| GET | /api/v2/health | Честное состояние lifecycle, DB, AI config, browser |
| GET / POST | /api/v2/competitors | Список / создание |
| GET / PATCH / DELETE | /api/v2/competitors/{competitor_id} | Detail / редактирование / удаление |
| POST | /api/v2/competitors/{competitor_id}/sources/text | Текст и auto-analysis |
| POST | /api/v2/competitors/{competitor_id}/sources/file | Multipart image/PDF и auto-analysis |
| POST | /api/v2/competitors/{competitor_id}/sources/url | Capture и auto-analysis |
| GET / DELETE | /api/v2/sources/{source_id} | Detail / удаление |
| POST | /api/v2/sources/{source_id}/reanalyze | Новая analysis текущего snapshot |
| POST | /api/v2/sources/{source_id}/refresh | URL-only: новый snapshot и analysis |
| GET | /api/v2/sources/{source_id}/snapshots/{snapshot_id}/artifact | DB-owned image/PNG preview |
| GET | /api/v2/competitors/{competitor_id}/analyses | История analyses |
| GET | /api/v2/analyses/{analysis_id} | Сохранённая analysis |
| POST | /api/v2/competitors/{competitor_id}/aggregate-analysis | CompetitorAnalysis; aggregate, snapshot_id=null |
| POST | /api/v2/comparisons | ComparisonResult для 2–5 distinct competitor_ids с persisted aggregates |

JSON errors: `{"error":{"code":"...","message":"...","details":null}}` с HTTP 400/404/413/422/500/502/504. DELETE success: 204; create/analysis success: 201. SQLite — единственное хранилище runtime state; файлы uploads/screenshots — артефакты сохранённых sources/snapshots. GET comparison пока не является публичным контрактом: запись доступна через repository, UI отображает POST result.
