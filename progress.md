# Progress Log

## 2026-03-20 Session

### Completed
- Created project directory:
  - `/Users/opener/wq/BRAIN_AI打工人Mac_Linux版本/auto-opener-miner`
- Initialized planning files:
  - `task_plan.md`
  - `findings.md`
  - `progress.md`
- Drafted full phased plan and draft data contracts for user review.
- Read openerminer reference (worldquant-miner/consultant-templates-api) and expanded plan with architecture, state machine, and optional adaptive discovery phase.
- Collected user decisions on dedup key, JSON format, TUI framework, credentials, and adaptive discovery scope.
- Built P1 skeleton (package layout, config, logging, TUI entry, stubs).
- Added `pyproject.toml`, `requirements.txt`, and default config.
- Implemented P2 template filler (init + expand commands) and generated demo factors.
- Implemented P3 factor viewer (list/show/validate/edit).
- Implemented P4 submitter core (brain adapter, state init/run/status, checkpointing).
- Implemented P5 backfill command (brain mode).
- Implemented P6 factor library (SQLite archive + dedup queries).
- Implemented P7 metadata downloader (operators/settings/datafields).
- Added demo flow script for P8.
  - Brain submitter + library archive validated.
- Added minimal Web UI (single-page) with API endpoints.
- Added cache-fill for templates using local datafields cache + Web UI entry.
- Refined Web UI to responsive layout and added online template editor (load/save/validate).

### In Progress
- None.

### Notes
- P0 architecture freeze confirmed complete.
- `python -m aom config` failed because `python` is not available; `python3 -m aom config` works.

### Pending
- Finalize architecture decisions from user feedback.
- Start scaffold implementation after approval.

## 2026-03-22 Session

### Completed
- Triaged AI endpoint timeout handling and added resilient LLM request retry/fallback (OpenAI-compatible -> Gemini).
- Fixed DataFields selector initialization and dataset interaction regressions in Web UI.
- Converted DataFields selector from strict modal behavior to page-embedded full-width panel behavior.
- Made "响应结果" panel default-collapsed.
- Collected new "梦到的alpha" requirements and updated planning docs.
- Located score extraction path from stored results (`alpha.is.sharpe`, `alpha.is.fitness`).

### In Progress
- None.

### Completed (DreamAlpha Backend)
- Added backend daemon module:
  - `aom/modules/dream_alpha/engine.py`
  - Cursor/seed/high-template files under `runs/`.
  - 24h loop semantics with graceful stop.
- Added robust error handling:
  - generation/simulation stage isolation
  - error counters + rolling events
  - error notification cooldown
- Added notification support:
  - high-score template push
  - error/fatal push
  - start/stop lifecycle push
- Added API control endpoints:
  - `POST /api/dream-alpha/start`
  - `POST /api/dream-alpha/stop`
  - `POST /api/dream-alpha/status`
- Added Web UI control panel (interface only):
  - Start/Stop/Status buttons
  - thresholds/files/interval inputs
  - live status polling
- Validation:
  - Python compile check passed
  - JS syntax check passed
  - offline daemon smoke test passed (start/status/stop + error path)
