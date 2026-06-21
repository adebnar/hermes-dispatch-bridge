#!/bin/bash
# Launches the Hermes Dispatch bridge using the project venv.
set -e
cd "$(cd "$(dirname "$0")/.." && pwd)"
UV="${UV_BIN:-/opt/homebrew/bin/uv}"
[ -x "$UV" ] || UV="$(command -v uv)"
exec "$UV" run --python .venv uvicorn app.main:app --host 0.0.0.0 --port 8099
