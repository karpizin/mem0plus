# mem0plus

`memory-runtime` — отдельный сервисный контур для `mem0plus`, который строится поверх `mem0-core`.

На этом этапе `mem0plus` — это product/runtime name, а каталог `memory-runtime/` пока сохраняется как технический путь для совместимости с текущими скриптами, Docker и pilot tooling.

На текущем этапе это scaffold первой версии сервиса:

- FastAPI приложение
- конфигурация через environment variables
- health endpoint
- namespace, event, recall, and adapter APIs
- Dockerfile
- Docker Compose для локального старта
- минимальная test harness
- `Makefile` с базовыми dev и quality командами

## Структура

```text
memory-runtime/
  app/
  tests/
  .env.example
  Makefile
  Dockerfile
  docker-compose.yml
  pyproject.toml
```

## Конфигурация

Скопируй шаблон переменных окружения:

```bash
cd memory-runtime
cp .env.example .env
```

Основные переменные:

- `MEMORY_RUNTIME_APP_NAME`
- `MEMORY_RUNTIME_ENV`
- `MEMORY_RUNTIME_DEBUG`
- `MEMORY_RUNTIME_API_PORT`
- `MEMORY_RUNTIME_WORKER_POLL_SECONDS`
- `MEMORY_RUNTIME_POSTGRES_DSN`
- `MEMORY_RUNTIME_REDIS_URL`
- `MEMORY_RUNTIME_MEM0_BRIDGE_ENABLED`
- `MEMORY_RUNTIME_MEM0_BASE_URL`
- `MEMORY_RUNTIME_MEM0_API_KEY`
- `MEMORY_RUNTIME_SENSITIVE_MEMORY_POLICY`
- `MEMORY_RUNTIME_MASK_SENSITIVE_MEMORY_OUTPUTS`

По умолчанию локальный scaffold использует SQLite-файл для безопасного старта без внешней БД.
Для Docker и реального runtime используется явный Postgres DSN из `.env`.
`core bridge` по умолчанию выключен и включается только явной конфигурацией.
Локальные pilot/eval CLI теперь сначала используют `memory-runtime/.venv/bin/python`, если он существует, и только потом fallback-ятся на системный Python.
Для sensitive memory policy сейчас поддерживаются два режима:
- `reject` — безопасный режим по умолчанию; obvious secrets не попадают в durable memory
- `mark` — sensitive facts можно сохранить, но они помечаются как sensitive и по умолчанию маскируются в recall/adapters как `[sensitive] hidden content`

## Установка dev-зависимостей

```bash
cd memory-runtime
python3 -m pip install -e '.[dev]'
```

## Локальный запуск

```bash
cd memory-runtime
make run
make run-worker
```

## Docker Compose

```bash
cd memory-runtime
cp .env.example .env
docker compose up --build
```

Сервис будет доступен на:

- `http://localhost:8080`
- `http://localhost:8080/healthz`
- `http://localhost:8080/docs`

Compose baseline теперь поднимает:

- `memory-api`
- `memory-worker`
- `postgres`
- `redis`

Readiness baseline теперь включает:

- healthcheck для `postgres`
- healthcheck для `redis`
- heartbeat-based healthcheck для `memory-worker`
- `service_healthy` dependencies для API и worker
- migration-aware startup path для Postgres, чтобы runtime не расходился со схемой Alembic при локальном compose-запуске
  и чтобы Docker image мог сам применить/stamp schema state, потому что `alembic.ini` и `migrations/` теперь входят в image

Для первого живого MVP-пилота с `OpenClaw` смотри runbook:
- [mem0plus-openclaw-pilot-runbook.md](/Users/slava/Documents/mem0-src/docs/core-concepts/mem0plus-openclaw-pilot-runbook.md)
- Для практического использования MCP смотри [mem0plus-mcp-guide.md](/Users/slava/Documents/mem0-src/docs/core-concepts/mem0plus-mcp-guide.md)

## API surface

Уже реализованы:

- `GET /metrics`
- `POST /mcp/{client_name}/http/{user_id}`
- `POST /v1/namespaces`
- `GET /v1/namespaces/{namespace_id}`
- `POST /v1/namespaces/{namespace_id}/agents`
- `POST /v1/events`
- `POST /v1/recall`
- `POST /v1/recall/feedback`
- `GET /v1/observability/stats`
- `POST /v1/adapters/openclaw/bootstrap`
- `POST /v1/adapters/openclaw/events`
- `POST /v1/adapters/openclaw/recall`
- `POST /v1/adapters/openclaw/search`
- `GET /v1/adapters/openclaw/memories`
- `GET /v1/adapters/openclaw/memories/{memory_id}`
- `PATCH /v1/adapters/openclaw/memories/{memory_id}`
- `DELETE /v1/adapters/openclaw/memories/{memory_id}`
- `POST /v1/adapters/bunkerai/bootstrap`
- `POST /v1/adapters/bunkerai/events`
- `POST /v1/adapters/bunkerai/recall`
- `POST /v1/adapters/bunkerai/search`
- `GET /v1/adapters/bunkerai/memories`
- `GET /v1/adapters/bunkerai/memories/{memory_id}`
- `PATCH /v1/adapters/bunkerai/memories/{memory_id}`
- `DELETE /v1/adapters/bunkerai/memories/{memory_id}`
- `DELETE /v1/adapters/bunkerai/memories`

Адаптерные endpoints фиксируют source-system contract для интеграций и работают поверх того же ingestion/recall pipeline.
Для `OpenClaw` добавлен отдельный runtime-contract: плагин сначала вызывает `bootstrap`, затем использует `events/search/list/get/delete` как transport-поверхность вместо прямого подключения к `mem0`.
Для BunkerAI действует такой же полный lifecycle contract. Compatibility-provider создаёт отдельный namespace на каждого авторизованного пользователя; `include_sessions=true` добавляет channel-local episodes в управление и экспорт, а collection-level `DELETE` очищает события, эпизоды, consolidated memory, фоновые задания и audit trail пользователя.
Long-term `search/list` в adapter contract теперь возвращают только durable `memory_units` и не подтягивают `session-space` или raw `episode` noise; session-scoped `episode` candidates доступны только при явном `session_id`, а уже консолидированные эпизоды не дублируются рядом с `memory_units`.

`PATCH /v1/adapters/openclaw/memories/{memory_id}` закрывает минимальный review-surface для `OpenClaw`: durable `memory_units` и session `episodes` можно обновлять, а `mark_incorrect` мягко убирает durable memory из active recall/list/search или удаляет session scratchpad из текущего session-surface.
Consolidation baseline теперь умеет:
- повышать decision-like `conversation_turn` до `decision`
- повышать procedural guidance до `procedure`
- canonicalize phrasing variants для более устойчивого merge
- supersede явные противоречащие long-term memories в пределах одного пространства вместо наивного сосуществования конфликтующих фактов
 - отклонять low-trust prompt-like и memory-poisoning candidates до их попадания в long-term memory
 - обрабатывать obvious secrets через configurable policy:
   - `reject` по умолчанию
   - или `mark`, когда sensitive memories сохраняются с `is_sensitive/sensitivity_reason` и маскируются в выдаче
В shared namespace `shared-space` доступен межагентно, при этом `agent-core` остается приватным.
`/metrics` отдает Prometheus-compatible экспорт counters и job gauges, а `/v1/observability/stats` дает JSON-срез для локальной диагностики и dashboard bootstrap.
Worker-derived operational counters (`jobs_*`, `consolidation_*`, `lifecycle_*`) теперь считаются из shared DB state (`jobs` и `audit_log`), а не только из process-local памяти.
`/v1/observability/stats` также показывает `oldest_pending_age_seconds` и `stalled_running_count`, чтобы было проще увидеть backlog или зависшие worker jobs.
Observability теперь включает и quality-oriented breakdown для promotion/rescue policy:
- `quality.decisions_by_outcome` для `promote / session_only / reject`
- `quality.*_reasons` для top причин demotion/rejection/promotion
- `quality.signal_flags` для `low_trust`, `transient`, `low_value`, `weak_candidate`, `rescue_applied`, `rescue_blocked`
- `quality.rescue.*` для breakdown по `rescue_trigger` и `rescue_block_reason`
Эти же данные экспортируются через `/metrics` как:
- `memory_runtime_promotion_decision_total`
- `memory_runtime_promotion_signal_total`
- `memory_runtime_promotion_novelty_state_total`
- `memory_runtime_rescue_event_total`
Recall observability теперь отдельно показывает performance/scalability breakdown:
- `performance.requests_observed_total`
- `performance.latency_buckets_ms`
- `performance.candidate_buckets`
- `performance.selected_buckets`
- `performance.external_candidate_buckets`
- `performance.brief_item_buckets`
- `performance.avg_candidate_count`, `avg_selected_count`, `avg_external_candidate_count`, `avg_brief_item_count`
Через `/metrics` это экспортируется как:
- `memory_runtime_recall_latency_bucket_total`
- `memory_runtime_recall_candidate_bucket_total`
- `memory_runtime_recall_selected_bucket_total`
- `memory_runtime_recall_external_candidate_bucket_total`
- `memory_runtime_recall_brief_item_bucket_total`
- `memory_runtime_recall_latency_ms_total`
- `memory_runtime_recall_latency_ms_max`
`/v1/recall/feedback` записывает usefulness signals, которые потом участвуют в последующем ranking.
При включенном `core bridge` runtime может синхронизировать long-term memories в `mem0` и использовать его как внешний recall source.
`POST /mcp/{client_name}/http/{user_id}` реализует stateless MCP Streamable HTTP facade поверх тех же runtime services.
Текущий MCP surface включает:
- `initialize`
- `tools/list`
- `tools/call` для `memory.recall`, `memory.search`, `memory.list_spaces`, `memory.get_observability_snapshot`, `memory.get_memory_unit`, `memory.ingest_event`, `memory.record_feedback`
- `resources/templates/list`
- `resources/read`
- `prompts/list`
- `prompts/get`
Для ресурса `latest agent brief` runtime сохраняет `recall_executed` entries в `audit_log`, чтобы MCP-клиенты могли читать последний recall без отдельного кеша.
Safe write tools идут только через существующие runtime paths: `memory.ingest_event` использует обычный ingestion pipeline, а `memory.record_feedback` использует существующий recall feedback path.
MCP counters (`mcp_requests_total`, `mcp_tool_calls_total`, `mcp_write_tool_calls_total`, `mcp_resource_reads_total`, `mcp_prompt_requests_total`, `mcp_errors_total`) экспортируются через `/metrics`.
Дополнительно observability теперь дает MCP breakdown по:
- JSON-RPC methods и их outcome
- tool/resource/prompt names и их outcome
- client_name
- latency buckets для MCP requests и tool calls
Для быстрой живой проверки MCP write/read loop есть `make mcp-smoke`, который выполняет `initialize -> tools/list -> memory.ingest_event -> memory.recall -> memory.record_feedback` и сохраняет отчет в `.artifacts/openclaw_mcp_smoke_report.json`.
Для быстрого старта интеграций есть готовый examples pack: [examples/mcp-memory-runtime](/Users/slava/Documents/mem0-src/examples/mcp-memory-runtime).

## Тесты

Базовые scaffold-тесты покрывают:

- конфигурацию по умолчанию и через environment variables
- health endpoint
- namespace and agent API baseline
- event ingestion and episode creation baseline
- recall baseline and `MemoryBrief` structure
- consolidation jobs, worker processing, and `memory_units` baseline
- consolidation regressions for semantic merge, kind inference, and contradictory memory supersede
- low-trust consolidation rejection for prompt-like long-term poisoning attempts
- lifecycle jobs, decay/archive/eviction baseline, and internal metrics counters
- adapter contracts for `OpenClaw` and `BunkerAI`
- OpenClaw runtime adapter coverage for bootstrap, search, list, get, review, and delete
- shared namespace e2e scenario for cross-agent memory exchange
- OpenClaw pilot e2e continuity flow
- Prometheus-style metrics exporter and observability stats endpoint
- recall feedback loop and usefulness-aware reranking
- core bridge unit coverage without external network dependency
- idempotent ingestion for duplicate events on the same dedupe key
- recall trace explainability with decisive selection signals
- golden compactness regression for low-budget memory briefs
- dialogue-based memory eval with manually annotated curated conversations, checking long-term promotion, demotion/rejection audit trail, and recall quality together
- configurable sensitive-memory policy coverage for both:
  - default `reject` mode
  - optional `mark + mask` mode for personal-memory use cases
- MCP Streamable HTTP facade with tools/resources/prompts contract, safe write tools, and transport validation
- отдельный MCP smoke runner для быстрой проверки guarded write/read flow на живом контуре
- MCP edge-case coverage для `shared-space` writes, приватности private `project-space` и guardrails на `agent-core`

Команды запуска:

```bash
cd memory-runtime
make test-unittest
make test
make test-e2e
make preflight
make pilot-smoke
make pilot-scenarios
make pilot-negative-scenarios
make openclaw-live-pilot
make pilot-scorecard INPUT=/path/to/live_scorecard.json
make quality-eval
make dialogue-eval
make lifecycle-eval
make continuity-benchmark
make performance-benchmark
make soak-benchmark
make load-benchmark
make compare-eval BEFORE=/path/to/before.json AFTER=/path/to/after.json
make snapshot-pilot NAME=before-live
make restore-pilot SNAPSHOT=before-live
make show-pilot-snapshots
make inspect-memories ARGS="--namespace-id <ns> --agent-id <agent> --limit 10"
make inspect-memory-lifecycle ARGS="--namespace-id <ns> --agent-id <agent> --limit 10"
make explain-recall ARGS="--namespace-id <ns> --agent-id <agent> --session-id live-debug --query 'What should be recalled?'"
make show-last-smoke
make show-last-quality-eval
make reset-pilot
```

`make preflight` прогоняет единый runtime preflight check и сохраняет JSON report в `.artifacts/openclaw_preflight_report.json`.
Localhost-facing pilot/eval tooling теперь использует HTTP clients с `trust_env=False`, чтобы системные proxy env vars не ломали сценарии на `127.0.0.1`.
Mixed `conversation_turn` payloads, в которых `OpenClaw` присылает `system + user + assistant`, теперь санируются на ingestion: `system`-сообщения отбрасываются, если в turn есть пользовательский или агентный контент. Это не дает system/bootstrap prompt попасть в durable memory как обычный факт.
`make pilot-smoke` поднимает Docker stack, прогоняет synthetic OpenClaw pilot contour и сохраняет JSON report в `.artifacts/openclaw_pilot_smoke_report.json`.
`make pilot-smoke` дополнительно сохраняет raw trace bundle в `.artifacts/pilot_traces/pilot-smoke/<run-name>/`.
`make pilot-scenarios` прогоняет 5 наиболее важных OpenClaw pilot scenarios и сохраняет JSON report в `.artifacts/openclaw_pilot_scenarios_report.json`.
`make pilot-scenarios` дополнительно сохраняет per-scenario trace bundle в `.artifacts/pilot_traces/pilot-scenarios/<run-name>/`.
`make pilot-negative-scenarios` прогоняет 3 негативных pre-live сценария и сохраняет JSON report в `.artifacts/openclaw_negative_pilot_scenarios_report.json`.
`make pilot-negative-scenarios` дополнительно сохраняет trace bundle в `.artifacts/pilot_traces/pilot-negative-scenarios/<run-name>/`.
`make openclaw-live-pilot` прогоняет real OpenClaw pilot pack через `openclaw agent --local`, затем собирает runtime evidence, recall traces и итоговый JSON report в `.artifacts/openclaw_live_pilot_report.json`.
Live runner теперь имеет отдельный process-timeout guard и durable-only validation для long-term adapter surface, чтобы зависший CLI или raw `episode` leakage не искажали verdict пилота.
Для live reliability runner использует отдельные execution knobs:
- базовый `--timeout-seconds` по умолчанию `180`
- отдельный `--continuity-timeout-seconds` по умолчанию `240` для cross-session continuity
- `--thinking` и `--continuity-thinking` для явного контроля reasoning budget на обычных и continuity-heavy turn'ах
- через `make openclaw-live-pilot ARGS=\"...\"` можно быстро подобрать профиль под конкретный live rerun, не редактируя код вручную
`make pilot-scorecard INPUT=...` считает live-pilot summary и verdict по заполненному JSON scorecard.
`make quality-eval` прогоняет 10 golden recall scenarios, печатает JSON report и служит регрессионным барьером для retrieval tuning.
В quality report теперь есть не только `pass/fail`, но и `required_hit_rate`, `forbidden_leak_rate`, `avg_selected_count` и `mean_scenario_score`.
`make dialogue-eval` прогоняет curated dialogue scenarios из `tests/fixtures/evals/dialogue_memory_scenarios.json`, создает для каждого отдельный namespace/session и проверяет:
- что попало в `long-term` memory
- что было демотировано или отклонено по `audit_log`
- что потом реально вспоминается через `recall`
Harness теперь поддерживает и scripted сценарии:
- последовательности `ingest -> feedback -> ingest` для проверки rescue-loop поведения
- ожидания по `audit.actions`, `audit.reasons` и `audit.signal_values`, чтобы различать `blocked rescue` и обычный demotion
Текущий curated corpus включает `15` вручную размеченных semi-real сценариев:
- architecture / standing procedures / timeout issues
- household and logistics chats в стиле Telegram/WhatsApp
- noisy preference, delivery, travel, maintenance и family-ops threads
- scripted rescue-flow cases с `negative` и `net-positive` feedback
- `must-reject` и `privacy-sensitive` cases, где memory poisoning, Wi-Fi/API credentials и другой obvious secret material должны остаться вне durable memory и recall
Это дает нам более честный baseline не только на коротких synthetic turns, но и на шумной бытовой переписке, где важно отделять durable signal от однодневного chatter.
Promotion decision layer теперь включает и первый `rescue loop`:
- повторяющиеся `session_only` кандидаты с причиной `insufficient_specificity_not_durable` могут позже продвинуться в durable memory
- `session_only` кандидаты с positive recall feedback тоже могут позже быть promoted
- negative feedback теперь тоже влияет на rescue:
  - повторяющийся слабый сигнал не rescue'ится, если у похожих прошлых эпизодов уже был negative feedback
  - mixed feedback требует net-positive history, иначе кандидат остается `session_only`
- при этом acknowledgement/status chatter и blocked origins не получают такого апгрейда “по инерции”
`make lifecycle-eval` прогоняет lifecycle scenarios для `decay/archive/evict/no-op` и печатает отдельный quality report по memory lifecycle.
`make continuity-benchmark` прогоняет cross-session continuity scenarios и проверяет, что durable architecture facts, standing procedures и integration context действительно переживают consolidation и возвращаются в recall.
`make performance-benchmark ARGS="--scenario all --memory-count 200 --query-count 5"` прогоняет performance/scalability benchmark pool и возвращает JSON-отчет с:
- `latency_ms` (`min / avg / p50 / p95 / max`)
- `candidate_count`
- `selected_count`
- `brief_chars`
Сейчас pool включает несколько профилей:
- `balanced_runtime`
- `procedure_heavy`
- `session_pressure`
- `integration_mix`
При желании можно прогонять и любой отдельный профиль через `--scenario <name>`.
`make soak-benchmark ARGS="--memory-count 500 --iterations 50 --scenario balanced_runtime"` выполняет repeated-recall soak run на одном большом memory pool и возвращает:
- `failure_rate`
- `latency_ms` (`min / avg / p50 / p95 / max`)
- `selected_count`
- `brief_chars`
`make load-benchmark ARGS="--memory-count 500 --concurrency 8 --rounds 5 --scenario balanced_runtime"` выполняет concurrent recall benchmark и возвращает:
- `total_requests`
- `failure_rate`
- `throughput_rps`
- `latency_ms` (`min / avg / p50 / p95 / max`)
- `selected_count`
- `brief_chars`
По умолчанию benchmark идет в `in-process` режиме, чтобы измерять сам runtime hot path без контейнерного drift. При необходимости можно явно указать `--base-url http://127.0.0.1:8080` и прогнать ту же нагрузку против живого контейнера.
Если benchmark запускается в `in-process` режиме против локального SQLite, лучше явно задать чистый DSN через `MEMORY_RUNTIME_POSTGRES_DSN=sqlite+pysqlite:////tmp/<name>.db MEMORY_RUNTIME_AUTO_CREATE_TABLES=true`, чтобы старые benchmark-базы не создавали schema drift.
Дополнительно retrieval и injection теперь имеют explicit high-density regressions:
- unit-тест на `~40` candidate memories проверяет, что brief остается релевантным и не засоряется шумом
- legacy recall test на `120` memories проверяет, что OpenClaw все равно подмешивает только компактный top-slice в рамках budget
- e2e adapter recall test теперь отдельно прогоняет `41`-memory mixed-slot scenario и `120`-memory durable-only scenario, проверяя конечный `brief`, `trace.selected_count` и отсутствие noisy leakage в итоговом recall ответе
`make compare-eval BEFORE=... AFTER=...` сравнивает два machine-readable eval report и показывает, где качество улучшилось, регрессировало или осталось без изменений.
Adversarial eval suite теперь покрывает instruction override, prompt exfiltration, explicit memory poisoning, mixed malicious content и benign control cases.
`make snapshot-pilot NAME=...` сохраняет SQL dump pilot-базы, observability snapshot и текущие pilot reports в `.artifacts/pilot_snapshots/<name>/`.
`make restore-pilot SNAPSHOT=...` восстанавливает snapshot обратно в compose Postgres и возвращает связанные pilot reports в `.artifacts/`.
`make show-pilot-snapshots` показывает доступные snapshots и их manifest metadata.

## Логи и диагностика

`memory-runtime` теперь пишет структурированные JSON-логи через стандартный Python logger.
Это помогает разбирать непонятные состояния в тестах и в live pilot без ручного воспроизведения.

Ключевые события, которые уже логируются:

- ingestion: `ingestion.received`, `ingestion.deduplicated`, `ingestion.persisted`
- consolidation: `consolidation.started`, `consolidation.promotion_decision`, `consolidation.created`, `consolidation.merged`, `consolidation.superseded`, `consolidation.demoted_session_only`, `consolidation.rejected`
- retrieval: `retrieval.started`, `retrieval.completed`, `retrieval.feedback_recorded`
- worker: `worker.job.started`, `worker.job.completed`, `worker.job.failed`, `worker.database.retrying`

Минимальный debug flow для локального разбора:

```bash
cd /Users/slava/Documents/mem0-src/memory-runtime
docker compose logs memory-api
docker compose logs memory-worker
```

Особенно полезно смотреть логи вместе с:

- `/v1/observability/stats`
- `/metrics`
- trace bundle из `.artifacts/pilot_traces/...`
`make inspect-memories ARGS="..."` печатает последние `memory_units` с key debugging полями прямо из БД.
`make inspect-memory-lifecycle ARGS="..."` печатает archived/superseded memory units для разбора forgetting и contradiction flows.
`make explain-recall ARGS="..."` запускает recall и печатает `selection_explanations` с `slot`, `decisive_signal` и `why`.
`make show-last-smoke` и `make show-last-quality-eval` показывают последние сохраненные pilot reports.
Trace bundles теперь включают raw recall payloads, observability snapshots и per-scenario manifests, чтобы live-debugging не зависел только от итогового summary report.
`make reset-pilot` сбрасывает compose stack, pilot reports и локальный pilot state для чистого повторного прогона, но не удаляет сохраненные snapshots.

Миграции:

```bash
cd memory-runtime
make migrate
```

## Quality workflow

Локальные проверки:

```bash
cd memory-runtime
make lint
make test
```

Fallback smoke suite без `pytest`:

```bash
cd memory-runtime
make smoke
```

Текущие scaffold-тесты совместимы и с `unittest`, и с `pytest`.
