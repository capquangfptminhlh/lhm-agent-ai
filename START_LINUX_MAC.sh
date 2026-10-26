#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
printf 'Office: http://127.0.0.1:7770/office\nBackend: http://127.0.0.1:7770/dashboard\n'
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7770