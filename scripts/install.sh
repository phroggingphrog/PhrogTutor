#!/usr/bin/env bash
# pi-tutor install: Python backend, web app, pi engine and the code sandbox.
# Usage: scripts/install.sh            (install)
#        scripts/install.sh --check    (only check the tools it needs)
set -euo pipefail
cd "$(dirname "$0")/.."
RUNCODE="${PI_TUTOR_RUNCODE_DIR:-$HOME/.local/share/pi-tutor-runcode}"
RUNCODE="${RUNCODE/#\~/$HOME}"

missing=0
need() { command -v "$1" >/dev/null 2>&1 && echo "ok       $1" || { echo "MISSING  $1 — $2"; missing=1; }; }
need python3 "Python 3.11+ for the backend"
need node    "Node 20+ for the web app"
need npm     "comes with Node"
need bun     "runs the pi engine: https://bun.sh"
need bwrap   "bubblewrap, the code sandbox (apt install bubblewrap)"
need prlimit "util-linux, memory/CPU limits for sandboxed code"
command -v Rscript >/dev/null 2>&1 && echo "ok       Rscript" || echo "optional Rscript — R code blocks need R (apt install r-base)"
[ "${1:-}" = "--check" ] && exit "$missing"
[ "$missing" = 0 ] || { echo "Install the missing tools first."; exit 1; }

python3 -m venv .venv
.venv/bin/pip install -q -e .
(cd pi_engine && bun install)
[ -f pi_engine/agent-dir/models.json ] || cp pi_engine/agent-dir/models.example.json pi_engine/agent-dir/models.json
(cd web && npm ci && npm run build)
mkdir -p "$RUNCODE/terminal-home"
python3 -m pip install -q --target "$RUNCODE/pylibs" numpy pandas matplotlib scipy
[ -f .env ] || cp .env.example .env
echo "Done. Edit .env and pi_engine/agent-dir/models.json (gateway URL, key, models), then start:"
echo "  .venv/bin/python -m uvicorn deeptutor.api.main:app --port 8001   and   (cd web && npx next start -p 3782)"
