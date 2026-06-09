#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export DATABASE_URL="${DATABASE_URL:-sqlite:///tmp.db}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-dummy}"
export SECRET_KEY="${SECRET_KEY:-dummy}"
export PYTHONPATH="${PYTHONPATH:-backend}"

backend/venv/bin/python -m pytest tests/test_dommer_b_regression.py "$@"
