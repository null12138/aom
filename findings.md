# Findings

## 2026-03-20

### User Requirements Captured
- Need a new folder: `auto-opener-miner` under current workspace.
- Need one-stop WorldQuant tool with 4 modules:
  - Factor submitter with JSON input, bulk submit, status mechanism, post-submit backfill.
  - Template filler including template editor + rule-based expansion.
  - Factor viewer for JSON inspection and editing.
  - Factor library to archive fully submitted JSON into SQLite with dedup support.
- User explicitly requested:
  - First deliver an overall development plan for review.
  - Implement after approval.

### Constraints Inferred
- Status updates and data write-back must be deterministic and resumable.
- Output from template filler must be in fixed format compatible with submitter.
- Dedup likely requires canonical expression + simulation settings identity key.

### Openerminer Notes (Reference Read)
- Emphasizes exploration/exploitation loop (UCB / bandit) for template selection.
- Architecture layers: AI template generation, data cache, decision engine, execution, learning.
- Concurrent simulations + reward function based on Sharpe/Fitness/Turnover.

### Unknowns
- Final preferred interface style: CLI-only or include Web UI in v1.
- Exact JSON formats currently used by user.
- Platform API boundary and credential management details.

### Decisions Confirmed (2026-03-20)
- Dedup key: `expression + settings`.
- Factor JSON: internal wrapper schema + export.
- TUI: `textual`.
- Credentials: config file (env override allowed).
- No adaptive discovery in v1.
- Allowed to borrow small utilities/patterns from root projects.

### Template Filler (v0.1)
- Placeholders format: `<name/>` with `fill_rules` mapping to list of strings.
- Expansion creates cartesian product, optional `--max` to limit combinations.
- Output factors include `schema_version`, `factor_id`, `expression`, `settings`, `priority`, `source_template_id`, `tags`.
- Cache-fill: use local datafields cache to populate `fill_rules` by rules (contains/exclude/dataset/regex).

### Factor Viewer (v0.1)
- Commands: `list`, `show`, `validate`, `edit`.
- `edit` supports nested keys like `settings.delay=0`.

### Submitter Core (v0.1)
- State file stores `queue`, `completed`, `failed`, `in_flight` lists with per-item status.
- Dedup via fingerprint (expression + settings).
- Brain adapter supported via API client (强制登录).

### Result Backfill (v0.1)
- `submit backfill` updates completed/in-flight items with fetched results.
- Brain mode only (requires credentials).

### Factor Library (v0.1)
- SQLite archive with `factors` + `submissions` tables.
- Archive from submit state or factor list; supports existence checks.

### Metadata Downloader (v0.1)
- `meta operators`, `meta settings`, `meta datafields` supported.
- Requires BRAIN credentials in config.

### Web UI (v0.1)
- Single-page UI served by `aom web` with responsive layout.
- Includes online template editor (load/save/validate) and cache-fill.

## 2026-03-22

### New Requirement: 梦到的alpha (Backend-first)
- User requests a continuous 24h alpha discovery loop.
- Web UI must be interface only; core logic must run in backend services.
- Loop lifecycle:
  - LLM generate candidates from selected datafields + user-provided seed library.
  - Submit for simulation backtest.
  - Feed returned `sharpe` / `fitness` back into loop memory.
  - Keep candidates with `abs(sharpe) > 1` and `fitness > 1` into seed library.
  - If `sharpe > 1.58` and `fitness > 1`, push template message to `https://tgpusher.opener.eu.org/?msg=xxx`.
- Additional constraint:
  - Must be robust and auto-handle failures/retries.
  - Errors/issues must also be pushed as notifications.

### Technical Discovery
- Existing submit adapter already returns full alpha payload via:
  - `BrainApiAdapter.submit` -> `BrainClient.simulate` -> `BrainClient.get_alpha`.
- Existing metric JSON in SQLite indicates useful fields are commonly under:
  - `result.alpha.is.sharpe`
  - `result.alpha.is.fitness`
  - (Fallbacks may still be needed for structural variance.)

### Implementation Notes (2026-03-22)
- New backend module implemented:
  - `aom/modules/dream_alpha/engine.py`
  - Class: `DreamAlphaDaemon`
- Loop mechanics:
  - LLM generation (AlphaGenerator) with seed-context injection into prompt.
  - Simulation via `BrainClient.simulate` and score extraction.
  - Accepted rule: `abs(sharpe) > threshold && fitness > threshold`.
  - High-template rule: `sharpe > template_threshold && fitness > threshold`.
- Persistence:
  - cursor: `runs/dream_alpha_cursor.json`
  - seed library: `runs/dream_alpha_seed_library.json`
  - high templates: `runs/dream_alpha_high_templates.jsonl`
- API integration:
  - `POST /api/dream-alpha/start|stop|status` in `aom/web/server.py`.
- Notification:
  - Supports `notify_url` with auto `msg` query build.
  - Pushes start/stop/high-template/error; error pushes are cooldown-throttled.
