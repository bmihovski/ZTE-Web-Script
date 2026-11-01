#!/bin/sh
# Deploy the keeper to the router - a thin wrapper around tools/deploy.py (check, backup, upload, restart, verify, rollback).
# It uploads only the code, never disk1/zte-keeper/keeper/data/, and puts the backup back when the verification fails.
#   ./deploy.sh                       tests first, then upload + restart (router: admin@192.168.6.1)
#   ./deploy.sh --host me@192.168.6.1 another SSH user / address
#   SKIP_TESTS=1 ./deploy.sh          skip pytest
# Other commands: python3 tools/deploy.py check|status|logs|backup|verify|rollback
set -eu
cd "$(dirname "$0")"
PY=python3
[ -x .venv/bin/python3 ] && PY=.venv/bin/python3
[ "${SKIP_TESTS:-}" = 1 ] && set -- --skip-tests "$@"
exec "$PY" tools/deploy.py deploy "$@"
