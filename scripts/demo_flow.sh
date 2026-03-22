#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python3 -m aom template init --out templates/demo.json
python3 -m aom template expand --template templates/demo.json --out generated/factors_demo.json
python3 -m aom factors validate --file generated/factors_demo.json
python3 -m aom submit run --file generated/factors_demo.json --state runs/submit_state_demo.json
python3 -m aom submit status --state runs/submit_state_demo.json
python3 -m aom library init --db db/factor_library.db
python3 -m aom library archive --db db/factor_library.db --state runs/submit_state_demo.json
python3 -m aom library stats --db db/factor_library.db
