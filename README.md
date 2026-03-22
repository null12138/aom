# Auto Opener Miner

A one-stop WorldQuant workflow tool (TUI-first) with modules for template filling, factor viewing, submission, and a factor library.

## Quickstart

1. Install deps:

```bash
pip install -r requirements.txt
```

2. Configure credentials:

- Edit `config/aom.toml`
- Or set `AOM_BRAIN_USERNAME` and `AOM_BRAIN_PASSWORD`

3. Run TUI:

```bash
python3 -m aom
```

Run Web UI:

```bash
python3 -m aom web --host 127.0.0.1 --port 8000
```

Web UI includes an online template editor (load/save/validate) and cache-fill.

## Template Filler

模板占位符使用 `<name/>` 格式，例如 `divide(<x/>, add(1, <y/>))`。

Create a skeleton template:

```bash
python3 -m aom template init --out templates/demo.json
```

Expand a template to factor list:

```bash
python3 -m aom template expand --template templates/demo.json --out generated/factors_demo.json
```

Override settings (JSON string):

```bash
python3 -m aom template expand \
  --template templates/demo.json \
  --settings-json '{"region":"USA","delay":1}'
```

Fill template from cached datafields:

```bash
python3 -m aom template cache-fill \
  --template templates/demo.json \
  --datafields metadata/datafields.json \
  --rules-json '{"x":{"contains":["eps","earn"],"limit":10},"y":{"contains":["rev","sales"],"limit":10}}' \
  --out templates/demo_filled.json
```

## Factor Viewer

List factors:

```bash
python3 -m aom factors list --file generated/factors_demo.json
```

Show one factor:

```bash
python3 -m aom factors show --file generated/factors_demo.json --id demo-0001
```

Edit fields:

```bash
python3 -m aom factors edit --file generated/factors_demo.json --id demo-0001 --set priority=50 --set settings.delay=0
```

Validate file:

```bash
python3 -m aom factors validate --file generated/factors_demo.json
```

## Submitter

Create state (optional):

```bash
python3 -m aom submit init --file generated/factors_demo.json --state runs/submit_state_demo.json
```

Create state with library dedup:

```bash
python3 -m aom submit init --file generated/factors_demo.json --state runs/submit_state_demo.json --library db/factor_library.db
```

Submitter (requires credentials):

```bash
python3 -m aom submit run --state runs/submit_state_demo.json --max-wait 1800
```

Backfill results:

```bash
python3 -m aom submit backfill --state runs/submit_state_demo.json
```

Check status:

```bash
python3 -m aom submit status --state runs/submit_state_demo.json
```

## Factor Library

Initialize db:

```bash
python3 -m aom library init --db db/factor_library.db
```

Archive from submit state:

```bash
python3 -m aom library archive --db db/factor_library.db --state runs/submit_state_demo.json
```

Archive from factor list:

```bash
python3 -m aom library archive --db db/factor_library.db --file generated/factors_demo.json
```

Check if factor exists:

```bash
python3 -m aom library check --db db/factor_library.db --expression 'divide(field_a, add(1, field_c))' --settings-json '{"region":"USA"}'
```

## Metadata Downloader

Download operators:

```bash
python3 -m aom meta operators --out metadata/operators.json
```

Download settings options:

```bash
python3 -m aom meta settings --out metadata/settings_options.json
```

Download datafields:

```bash
python3 -m aom meta datafields --out metadata/datafields.json --region USA --delay 1 --universe TOP3000
```

## CLI

```bash
aom config
aom web --host 127.0.0.1 --port 8000
aom template init --out templates/demo.json
aom template expand --template templates/demo.json
aom factors list --file generated/factors_demo.json
aom submit run --file generated/factors_demo.json --state runs/submit_state_demo.json
aom library stats --db db/factor_library.db
```

## Demo Script

```bash
bash scripts/demo_flow.sh
```

Other subcommands are stubbed in v0.1 and will be extended as needed.
