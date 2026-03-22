# Auto Opener Miner - Task Plan

## Goal
Build a one-stop WorldQuant workflow tool with 4 modules:
1. Factor submitter (batch submit + state machine + result backfill)
2. Template filler (template editor + expansion engine)
3. Factor viewer (inspect/edit JSON factors)
4. Factor library (archive submitted JSON into SQLite + dedup before submit)

## Scope Baseline
- Language: Python 3.10+
- Storage:
  - Working JSON files (pipeline state)
  - SQLite archive (`factor_library.db`)
- Interface:
  - v1 must be TUI (terminal user interface)
  - Keep CLI-compatible internals for automation
- Submit semantics:
  - Submitter means backtest submission (simulation), not production submit
- Ordering:
  - Submit strictly by priority
- Project policy:
  - Independent new project, only API protocol can be referenced from current repo
  - Allow limited borrowing from other root projects (small utilities or patterns, not wholesale copy)

## Architecture Outline (Draft)
- `core/`: domain models (Template, Factor, RunState), schema versions, validators, fingerprinting.
- `api/`: BRAIN HTTP client, auth/session, retry/backoff, rate-limit handling.
- `modules/template_filler`: load templates, expand rules, validate expressions.
- `modules/factor_viewer`: list/inspect/edit JSON factors safely.
- `modules/submitter`: queue, state machine, idempotency, checkpointing.
- `modules/library`: SQLite archive + dedup queries.
- `modules/metadata`: operators/data-fields downloader + local cache.
- `ui/`: TUI screens and CLI wrappers (same core services).

## Interface & Commands (Draft)
- CLI entry name: `aom` (placeholder; to confirm)
- `aom template` -> edit/expand templates, output factor JSON.
- `aom factors` -> list/inspect/validate/edit factor JSON.
- `aom submit` -> run batch submit with checkpoint/resume.
- `aom library` -> query/archive/dedup.
- `aom meta` -> download operators/datafields caches.
- `aom config` -> show/edit config, credential checks.

## Phases

| Phase | Status | Deliverable | Acceptance |
|---|---|---|---|
| P0 - Architecture freeze | complete | data contracts + directory structure + module boundaries | all JSON schemas and state transitions reviewed |
| P1 - Project skeleton | complete | package layout + config + logging + TUI entry | `python -m` starts TUI |
| P2 - Template filler | complete | template schema + editor command + expansion command | can generate valid factor JSON list |
| P3 - Factor viewer | complete | inspect/list/validate/edit commands | can read/edit/write JSON safely |
| P4 - Submitter core | complete | batch submit pipeline + checkpoint + retry + idempotency | interrupted run can resume without duplicate submit |
| P5 - Result backfill | complete | update factor records with returned platform data | each submitted factor marked and enriched |
| P6 - Factor library | complete | archive to SQLite + dedup query API | submitter can skip duplicates |
| P7 - Metadata downloader | complete | download operators and data-fields from WQ | outputs JSON files for local use |
| P8 - Integration & QA | complete | end-to-end flow + tests + sample docs | brain submitter scripts validated |

## Submitter State Machine (Draft)
- `queued` -> `prepared` -> `submitted` -> `running` -> `completed`
- `failed` is terminal unless `retryable=true`, then `failed` -> `queued`
- `backfilled` marks `completed` items with fetched metrics

## Data Contracts (Draft v0)
- `templates/*.json`: `schema_version`, `template_id`, `template`, `fill_rules`, `metadata`
- `generated/factors_*.json` (list): `schema_version`, `factor_id`, `expression`, `settings`, `priority`, `source_template_id`, `tags`
- `runs/submit_state_*.json`: `schema_version`, `run_id`, `created_at`, `config`, `queue`, `in_flight`, `completed`, `failed`, `stats`
  - each item: `factor_id`, `expression`, `settings`, `priority`, `fingerprint`, `status`, `submission_id`, `alpha_id`, `result`, `backfilled_at`
- `archive/final_submitted_*.json`: `schema_version`, `factor_id`, `expression`, `settings`, `submission_id`, `alpha_id`, `result`, `submitted_at`
- `db/factor_library.db` `factors`: `fingerprint`, `expression`, `settings_json`, `status`, `metrics_json`, `created_at`, `last_submitted_at`
- `db/factor_library.db` `submissions`: `run_id`, `submission_id`, `status`, `result_json`, `updated_at`

## Dedup Strategy (Draft)
- Fingerprint: `sha256(canonical_expression + canonical_settings + type)`
- Canonicalization: trim whitespace, normalize operators, stable JSON sort for settings.

## Risk Register
| Risk | Impact | Mitigation |
|---|---|---|
| API/network instability | partial submission, state drift | checkpointing + retry policy + idempotency keys |
| JSON schema drift | runtime failures | strict schema validation + version field |
| duplicate submissions | wasted quota | pre-submit dedup against SQLite + hash key |
| long batch jobs interrupted | lost progress | transactional state persistence every item |

## Decisions (Confirmed)
1. Dedup strictness: `expression + settings` composite key.
2. Factor JSON format: internal wrapper schema + export to BRAIN payload as needed.
3. TUI framework: `textual`.
4. Credentials: config file (support env override).
5. Adaptive discovery (bandit): not needed for v1 (defer to future backlog).

## Backlog (Deferred)
1. Adaptive discovery (bandit loop) for template exploration.

## Error Log
| Error | Attempt | Resolution |
|---|---|---|
| `python` not found when running `python -m aom config` | 1 | Use `python3` instead. |

---

## 2026-03-22 Extension: "梦到的alpha" Continuous Loop

### Goal
Add a 24h continuous alpha ideation loop in Web UI:
- LLM generates candidate alphas from selected datafields + user seed library
- Submit each candidate for backtest
- Feed Sharpe/Fitness back into loop memory
- Keep only qualified candidates (`abs(sharpe) > 1` and `fitness > 1`) into seed library
- For high performers (`sharpe > 1.58` and `fitness > 1`) push template to `https://tgpusher.opener.eu.org/?msg=xxx`

### Phases

| Phase | Status | Deliverable | Acceptance |
|---|---|---|---|
| D1 - Data model & storage | complete | local JSON store for loop state, seed library, accepted/high-score logs | state can survive restart |
| D2 - Dream loop engine | complete | background worker thread with start/stop/status | can run continuously and stop gracefully |
| D3 - Integration with LLM + backtest | complete | generate->simulate->score pipeline | each cycle returns sharpe/fitness and updates memory |
| D4 - API endpoints | complete | `/api/dream-alpha/start|stop|status` | UI can control worker |
| D5 - Web UI panel | complete | controls, thresholds, live counters/logs | user can run loop without CLI |
| D6 - Push notifier | complete | high-score template push to tgpusher URL | high-score events emitted successfully |
| D7 - Validation | complete | syntax check + smoke run path | no JS/Python syntax regressions |

### Design Notes
- Use backend daemon thread (within web server process) for 24h loop.
- Use existing `AlphaGenerator` and `BrainClient` to minimize duplicated logic.
- Keep loop batch small (`1-3`) to reduce API pressure; sleep between rounds.
- Seed library file stores expressions and metadata (score history, source, timestamps).
