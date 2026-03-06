# AI Assignment Checker

AI Assignment Checker — сервис для автоматизированной проверки учебных заданий.

Проект принимает работы кандидатов, проводит их через конвейер нормализации, LLM-оценки и доставки обратной связи, а затем формирует результаты для кандидата и организатора.

В репозитории уже есть инфраструктурный каркас и контракты, на которых строится продукт:

- единая точка входа приложения с флагами ролей;
- skeleton-режим запуска для API и worker-ролей;
- fail-fast валидация роли;
- health/readiness эндпоинты и структурированное логирование старта;
- Docker Compose baseline с внешним one-shot migrator;
- локальный runtime-контракт без Docker на базе `uv` и `.venv`;
- слоистый skeleton с interface-first контрактами repositories/clients;
- persistence-backed onboarding контракты для candidates и assignments;
- OpenAPI/Swagger request/response контракты с типизированными Pydantic-схемами.

## Слоистая архитектура

- `app/api`: транспортный слой api
- `app/services`: wiring/bootstrap зависимостей
- `app/workers`: фоновые задачи с claim/process/finalize
- `app/domain`: общие контракты и модели
- `app/repositories`: asyncpg Postgres adapter + in-memory deterministic stub
- `app/clients`: реализации клиентов

Направление зависимостей: `api/services/workers -> domain contracts`, а инфраструктура подключается в bootstrap. См. `app/ARCHITECTURE.md`.
Точки расширения компонентов для будущей бизнес-логики перечислены в `app/COMPONENTS.md`.

Worker-роли в skeleton mode запускают фоновый polling loop. Каждый тик выполняет claim -> process -> finalize с no-op-safe хендлерами. По умолчанию (`INTEGRATION_MODE=stub`) зависимости детерминированно stub-овые; в `INTEGRATION_MODE=real` используются реальные интеграции по role/wiring (в том числе Telegram и S3).

Readiness теперь включает статус worker-loop и счетчики через `/ready`:

- `worker_loop_enabled`
- `worker_loop_ready`
- `worker_metrics` (`ticks_total`, `claims_total`, `idle_ticks_total`, `errors_total`)

Synthetic in-process проверки пайплайна доступны для инфраструктурной валидации:

- `POST /submissions/file` принимает файл плюс `candidate_public_id` и `assignment_public_id`, затем сохраняет артефакт `raw/`.
- `POST /internal/test/run-pipeline` запускает цепочку normalize -> evaluate -> deliver для указанной работы.
- `GET /submissions/{id}` возвращает текущее состояние, переходы и ссылки на артефакты для тестовых работ.

Быстрое локальное использование:

1. Создайте candidate и assignment, затем загрузите файл через `POST /submissions/file`.
2. Запустите synthetic pipeline через `POST /internal/test/run-pipeline`.
3. Получите итоговый trace через `GET /submissions/{submission_id}`.

Пример `curl` для шага 2:

```bash
curl -sS -X POST "http://localhost:8000/internal/test/run-pipeline" \
  -H "Content-Type: application/json" \
  -d '{"submission_id":"sub_01ABCDEF0123456789ABCDEF01"}'
```

Ожидаемые состояния:

- happy path заканчивается на `delivered`;
- fail-fast path заканчивается одним из `failed_normalization`, `failed_evaluation`, `failed_delivery`.

Ссылки на артефакты являются внутренними storage reference (`s3://...` ), а не публичными URL для скачивания.

Текущий onboarding flow:

- `POST /candidates` создает или переиспользует mapping идентичности кандидата.
- `POST /assignments` создает метаданные задания.
- `GET /assignments` возвращает список заданий (по умолчанию только активные).
- `POST /submissions` требует `candidate_public_id` и `assignment_public_id`.
- Telegram работает как входной брокер: `worker-ingest-telegram` обрабатывает `/start` через polling и отправляет подписанную ссылку `/candidate/apply`.
- Инжест отправок остается API-ориентированным (`POST /submissions/file` и `POST /submissions`).

Настройки ссылки входа через Telegram:

- `PUBLIC_WEB_BASE_URL` (базовый URL для подписанной apply-ссылки)
- `TELEGRAM_LINK_SIGNING_SECRET` (HMAC-секрет для подписи токена входа)
- `TELEGRAM_LINK_TTL_SECONDS` (время жизни токена в секундах)

Операционный E2E-паттерн для Telegram интеграции:

- Подготовка и startup-проверки (`--dry-run-startup`) выполняются до live-трафика.
- Перед live-проверкой допускается operator-assisted checkpoint: вручную подготовить канал/бота и env.
- После подтверждения setup выполняется проверка polling -> обработка -> outbound send и просмотр логов на ошибки.

Контракты публичных ID:

- candidate: `cand_<ULID>`
- assignment: `asg_<ULID>`
- submission: `sub_<ULID>`

Внутренние первичные ключи БД остаются `BIGSERIAL`; public IDs являются внешними API-идентификаторами.

Swagger/OpenAPI доступен по стандартным endpoint FastAPI (`/docs`, `/openapi.json`) и считается источником HTTP-контракта.

Обработка dead-letter пока не реализована; `dead_letter` сейчас выступает как терминальное persistence-состояние.

## Роли рантайма

- `api`
- `worker-ingest-telegram`
- `worker-normalize`
- `worker-evaluate`
- `worker-deliver`

`migrator` является внешним сервисом и не является app role (используется golang-migrate).

## Переменные окружения

Используйте `.env.example` в корне репозитория как канонический шаблон переменных.

Рантайм автоматически загружает `.env` при старте, если файл существует.
Приоритет значений: сначала `.env`, затем переменные процесса (process env) как override.

Шаблон делит переменные на три группы:

- уже используются рантаймом/тестами;
- уже присутствуют в postgres-сервисе docker-compose;
- используются рантаймом в `INTEGRATION_MODE=real` (S3, Telegram) или зарезервированы под следующие инкременты (LLM).

### Режим валидации runtime-конфига

- `RUNTIME_VALIDATION_MODE=dev` (по умолчанию): локально-дружественный режим со stub-ориентированными дефолтами.
- `RUNTIME_VALIDATION_MODE=strict`: fail-fast проверка только критичных зависимостей рантайма для активной роли.

`strict` проверяет:

- `api`: `DATABASE_URL`
- `worker-ingest-telegram`: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`
- `worker-normalize`: `DATABASE_URL`, `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`
- `worker-evaluate`: `DATABASE_URL`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`
- `worker-deliver`: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`

Опционально поддерживается `TELEGRAM_BOT_API_BASE_URL` (по умолчанию `https://api.telegram.org`); если задана, должна быть валидным `http(s)` URL.

Опциональные/инфраструктурные переменные с валидными дефолтами не должны сами по себе валить старт.

Скопировать пример локально:

```bash
cp .env.example .env
```

## Локальная настройка (uv + .venv)

```bash
uv venv .venv
uv sync --all-groups
```

Запуск роли в пустом режиме:

```bash
uv run python -m app.main --role api
```

Проверка старта в dry-run режиме:

```bash
uv run python -m app.main --role worker-evaluate --dry-run-startup
```

Параметры тюнинга worker polling (опционально):

- `WORKER_POLL_INTERVAL_MS` (по умолчанию: `200`)
- `WORKER_IDLE_BACKOFF_MS` (по умолчанию: `1000`)
- `WORKER_ERROR_BACKOFF_MS` (по умолчанию: `2000`)
- `WORKER_CLAIM_LEASE_SECONDS` (по умолчанию: `30`)
- `WORKER_HEARTBEAT_INTERVAL_MS` (по умолчанию: `10000`)

Запуск локальных smoke-проверок ролей:

```bash
make smoke-local
```

## Контракт запуска Docker Compose

Путь зависимостей в compose:

`postgres -> migrator -> app services`

Запуск prod-like стека:

```bash
docker compose -f docker-compose.yml up --build
```

Запуск быстрого локального dev-режима (default compose + override):

```bash
docker compose up --build
```

По умолчанию поднимаются `postgres`, `migrator` и `api`.

Запуск полного локального dev-режима с воркерами через профиль:

```bash
docker compose --profile full up --build
```

Примечания:
- `docker-compose.yml` остается prod-like (без локального mount исходников).
- `docker-compose.override.yml` является dev-слоем (mount-ы + dev-команды).

## Тесты

- все тесты: `make test`
- только unit: `make test-unit`
- только integration: `make test-integration`
- проверка типов: `make typecheck`

Postgres-backed integration тесты запускаются, когда БД доступна через `TEST_DATABASE_URL` (или `DATABASE_URL`) иначе они пропускаются.
В CI должен быть доступный Postgres, а покрытие Postgres-backed integration тестами должно считаться обязательным.
