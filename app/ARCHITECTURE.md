# Границы архитектурных слоев

Этот документ объясняет, как устроен рантайм, где должна жить бизнес-логика и где безопасно вносить изменения.

## 1) Обзор рантайма

Точка входа: `app/main.py`

- Процесс запускается с флагом `--role`.
- Поддерживаемые роли:
  - `api`
  - `worker-ingest-telegram`
  - `worker-normalize`
  - `worker-evaluate`
  - `worker-deliver`
- `migrator` является внешним one-shot сервисом и не относится к app role.

`app/main.py` валидирует роль, собирает зависимости через `app/services/bootstrap.py` и запускает FastAPI-приложение из `app/api/http_app.py`.

## 2) Слои и ответственность

- `app/api`
  - Только HTTP-транспорт (роуты, маппинг request/response, readiness/health).
  - Вызывает хендлеры из `app/api/handlers`.
  - Не должен содержать доменную логику.

- `app/services`
  - Сборка зависимостей/bootstrap.
  - Формирует runtime container с помощью ручного DI.

- `app/lib`
  - Инфраструктурные библиотеки, разделяемые адаптерами/хендлерами.
  - `app/lib/artifacts/*` отвечает за сериализацию артефактов, проверку версий и маппинг в storage.

- `app/workers`
  - Оркестрация воркеров:
    - polling runner
    - цикл claim/process/finalize
    - маппинг role-to-handler
  - Логика конкретных стадий находится в `app/workers/handlers`.

- `app/domain`
  - Базовые контракты, DTO, модели, use-case.
  - Основное место для production бизнес-логики.
  - Доступ к Artifact I/O через контракт `ArtifactRepository`.
  - Не должен импортировать transport/framework/SDK-specific модули.

- `app/repositories`
  - Persistence-адаптеры, реализующие доменные repository-контракты.
  - Включает asyncpg Postgres-адаптер и in-memory детерминированный stub.

- `app/clients`
  - Адаптеры внешних интеграций (storage, Telegram, LLM).
  - В текущем состоянии используются non-network stubs.

## 3) Направление зависимостей (строгое правило)

Направление должно оставаться таким:

`api/services/workers -> domain contracts`

Также:

- `repositories` и `clients` реализуют domain-контракты.
- `domain` не должен зависеть от `api`, `workers`, FastAPI, Uvicorn, provider SDK или DB drivers.
- `workers/services` должны использовать интерфейсы, а не конкретные инфраструктурные импорты в бизнес-коде.
- Политика совместимости artifact-контрактов по умолчанию `strict` (`ARTIFACT_COMPAT_POLICY=strict`).

## 4) Потоки API и воркеров

### API flow (role: `api`)

- Роуты определены в `app/api/http_app.py`.
- Роуты вызывают хендлеры из `app/api/handlers/*`.
- Хендлеры вызывают domain use-case (или stubs на текущем этапе).
- Ответы маппятся обратно в HTTP JSON.
- Выгрузки отдаются через API (`download_url` -> `/exports/{export_id}/download`), поэтому storage может оставаться приватным.

Ключевые роуты:

- `GET /health`
- `GET /ready`
- `POST /candidates`
- `POST /assignments`
- `GET /assignments`
- `POST /submissions`
- `GET /submissions/{submission_id}`
- `POST /submissions/file` (synthetic infra check)
- `GET /feedback`
- `POST /exports`
- `GET /exports/{export_id}/download`
- `POST /internal/test/run-pipeline` (synthetic infra check)

### Worker flow (worker-* roles)

- `app/workers/runner.py` запускает фоновый polling loop.
- Каждый тик вызывает `WorkerLoop.run_once()` из `app/workers/loop.py`.
- Для Telegram ingest в MVP используется polling-режим через `worker-ingest-telegram`.
- Telegram ingest только на входе: `/start` создает подписанную web-ссылку и не создает submissions/raw artifacts.
- Жизненный цикл `run_once()`:
  1. `claim_next(...)`
  2. `process(claim)` (хендлер роли)
  3. опционально `link_artifact(...)`
  4. `finalize(...)`

Зачем нужен каждый шаг:

- `claim_next(...)`
  - Выбирает один подходящий элемент работы для текущей стадии.
  - Не дает двум воркерам одновременно обработать один и тот же элемент.

- `process(claim)`
  - Выполняет stage-specific поведение (Telegram entry routing, normalize/evaluate/deliver и т.д.).
  - Возвращает `ProcessResult` с флагом успеха, detail и optional artifact metadata.
  - Стадия evaluate резолвит chain spec из `app/eval/chains/chain.v1.yaml` и использует domain chain execution (`app/domain/evaluation_chain.py`).

- `link_artifact(...)` (опционально)
  - Сохраняет ссылку/версию артефакта, если стадия сгенерировала выход.
  - Опционален, так как некоторые стадии меняют только состояние и не создают артефакты.
  - Artifact trace keys задокументированы в `app/domain/artifacts.py`.
  - Ключи 1:1 соответствуют стадиям, которые создают артефакты.

- `finalize(...)`
  - Фиксирует итог попытки стадии (успех/ошибка + detail).
  - Это терминальный шаг одной claim-попытки, на который опираются retries/next-stage logic.

Lease-поведение для долгих обработчиков:

- Перед каждым тиком рантайм периодически вызывает `reclaim_expired_claims(stage=...)`.
- Во время `process(claim)` `WorkerLoop` запускает heartbeat и продлевает lease через `heartbeat_claim(...)`.
- Если lease-владение теряется в середине обработки, тик завершается ошибкой stale-claim и успех не фиксируется.

Dead-letter (текущее ограничение):

- `dead_letter` сейчас только терминальное persistence-состояние.
- Рантайм переводит элементы в `dead_letter` после исчерпания попыток или reclaim просроченного lease.
- Отдельного dead-letter flow пока нет (triage/requeue/alerting API или worker).
- До production-rollout нужно добавить операционный dead-letter flow, а не полагаться только на retries.

Порядок выполнения важен:

- Сначала claim, затем process, затем finalize.
- Если `process` падает, `finalize` все равно фиксирует контекст ошибки (через exception path в runner/retry policy).
- Старайтесь держать `process` детерминированным и идемпотентным; retries могут повторно запускать ту же логическую задачу.

Схема последовательности:

```text
runner tick
   |
   v
WorkerLoop.run_once()
   |
   +--> repository.claim_next(stage, worker_id)
   |      -> WorkItemClaim | None
   |
   +--> role_handler.process(claim)
   |      -> ProcessResult(success, detail, artifact_ref?, artifact_version?)
   |
   +--> [if artifact_ref] repository.link_artifact(...)
   |
   +--> repository.finalize(item, stage, success, detail)
   |
   +--> return did_work=True

If claim_next returns None -> return did_work=False (idle/backoff path in runner).
```

Маппинг role handler находится в `app/workers/handlers/factory.py`.

## 5) Skeleton mode (текущее состояние)

- Адаптеры stub-овые, реальных сетевых вызовов нет.
- Worker-роли работают в непрерывном фоне (polling loop).
- `/ready` включает состояние runtime-воркера и счетчики:
  - `worker_loop_enabled`
  - `worker_loop_ready`
  - `worker_metrics`:
    - `ticks_total`
    - `claims_total`
    - `idle_ticks_total`
    - `errors_total`

Тюнинг runner через env:

- `WORKER_POLL_INTERVAL_MS` (по умолчанию `200`)
- `WORKER_IDLE_BACKOFF_MS` (по умолчанию `1000`)
- `WORKER_ERROR_BACKOFF_MS` (по умолчанию `2000`)

## 6) Где реализовывать новую логику

Используйте `app/COMPONENTS.md` как источник правды.

### Если добавляете новую API ручку

1. Добавьте/обновите роут в `app/api/http_app.py`.
2. Реализуйте хендлер в `app/api/handlers/*`.
3. Перенесите бизнес-правила в `app/domain/use_cases/*`.
4. Подключите зависимости через `app/services/bootstrap.py`.
5. Добавьте unit и integration тесты.

### Если добавляете новый worker

1. Реализуйте stage-логику в `app/workers/handlers/<stage>.py`.
2. Держите оркестрацию общей в `app/workers/loop.py`.
3. Держите polling-поведение в `app/workers/runner.py`.
4. Добавьте доменную логику в `app/domain/use_cases/*`.
5. Подключите новые зависимости через `app/services/bootstrap.py`.

### Если добавляете реальную внешнюю интеграцию

1. Определите/расширьте контракт в `app/domain/contracts.py`.
2. Реализуйте адаптер в `app/clients/*` или `app/repositories/*`.
3. Подключите реализацию в `app/services/bootstrap.py`.
4. Не вызывайте SDK/DB напрямую из domain use-case.

## 7) Чеклист готовности PR

Используйте этот список перед открытием PR:

- [ ] Бизнес-логика находится в `app/domain/use_cases/*`, а не в route/transport коде.
- [ ] Направление зависимостей валидно (`api/services/workers -> domain contracts`).
- [ ] Обновлены `COMPONENT_ID` и маппинги в `app/COMPONENTS.md`, если поведение изменилось.
- [ ] Unit-тесты покрывают изменения логики/контрактов.
- [ ] Integration-тесты покрывают изменения runtime/wiring.
- [ ] Если добавлена новая роль, обновлены role/stage/handler/compose/test matrices.
- [ ] Проходят `make test`, `make test-unit`, `make test-integration`, `make typecheck`.
- [ ] Кросс-трековые изменения вне основной зоны владения ссылаются на seam-контракт из `app/COMPONENTS.md` (`seam.*`).

## 8) Примечание о synthetic end-to-end

`POST /submissions/file` и `POST /internal/test/run-pipeline` предназначены только для инфраструктурной проверки.

Они проверяют, что routing, handlers, wiring и последовательность стадий работают вместе в одном процессе, без реальных persistence/integration зависимостей.

### 8.1) Быстрое использование

Рекомендуемая последовательность локальной проверки:

1. Создайте candidate и assignment через API-роуты.
2. Загрузите файл через `POST /submissions/file`.
3. Запустите synthetic chain через `POST /internal/test/run-pipeline`.
4. Проверьте финальное состояние/trace через `GET /submissions/{submission_id}`.

Минимальный пример вызова pipeline:

```bash
curl -sS -X POST "http://localhost:8000/internal/test/run-pipeline" \
  -H "Content-Type: application/json" \
  -d '{"submission_id":"sub_01ABCDEF0123456789ABCDEF01"}'
```

Семантика состояний:

- success path: `uploaded -> normalization_in_progress -> normalized -> evaluation_in_progress -> evaluated -> delivery_in_progress -> delivered`
- fail-fast path: пайплайн останавливается на первой неуспешной стадии и возвращает `failed_*`

### 8.2 Сделать когда-то потом

- Submission-level evaluation snapshot (`chain_digest` и resolved chain spec) отложен на post-MVP.
- Текущий evaluate flow резолвит default chain spec во время выполнения из `app/eval/chains/chain.v1.yaml`.
- Политика chain mismatch после rollout snapshot: начать с `warn-only` диагностики при digest mismatch, затем перейти к `strict-fail`, когда persistence стабилизируется.

## 9) Частые ошибки

### 9.1 Бизнес-логика в роуте вместо domain use-case

Правильно:

```python
@app.post("/submissions")
async def create_submission(payload: CreateSubmissionRequest):
    return await create_submission_with_candidate_handler(
        api_deps,
        source_external_id=payload.source_external_id,
        candidate_public_id=payload.candidate_public_id,
        assignment_public_id=payload.assignment_public_id,
    )
```

Неправильно:

```python
@app.post("/submissions")
async def create_submission(payload: dict[str, str] = Body(default={})):
    # business rule directly in transport layer
    score = int(payload["score"])
    if score > 7:
        return {"result": "pass"}
    return {"result": "fail"}
```

### 9.2 Зависимость domain от конкретных адаптеров

Правильно:

```python
def evaluate_submission(cmd: EvaluateSubmissionCommand, *, llm: LLMClient) -> EvaluateSubmissionResult:
    ...
```

Неправильно:

```python
from app.clients.stub import StubLLMClient

def evaluate_submission(cmd: EvaluateSubmissionCommand) -> EvaluateSubmissionResult:
    llm = StubLLMClient()
    ...
```

### 9.3 Stage-specific поведение в общем worker loop

Правильно:

```python
def process_claim(deps: WorkerDeps, *, claim: WorkItemClaim) -> ProcessResult:
    # worker.evaluate.process_claim
    ...
```

Неправильно:

```python
def run_once(self) -> bool:
    ...
    if self.role == "worker-evaluate":
        # stage-specific logic in generic orchestration
        ...
```

### 9.4 Transport-specific типы попали в domain

Правильно:

```python
@dataclass(frozen=True)
class PrepareExportResult:
    export_rows: list[ExportRowArtifact]
```

Неправильно:

```python
from fastapi.responses import JSONResponse

def prepare_export(...) -> JSONResponse:
    return JSONResponse({"ok": True})
```


