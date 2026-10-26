#!/usr/bin/env bash
# run the test suite the same way CI / the developer would.
# creates the venv on first run if it's missing.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "no .venv found, creating one..."
    python3 -m venv .venv
    .venv/bin/pip install -q -r requirements-dev.txt
fi

.venv/bin/python -m pytest tests/ -q
