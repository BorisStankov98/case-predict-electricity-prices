#!/usr/bin/env bash
# Build + run the forecasting pipeline in Docker (via docker compose), then open
# the HTML report on the host.
# Works whether invoked as `./solution/run.sh`, `bash solution/run.sh`, or
# `sh solution/run.sh`.
set -eu

# Repo root = parent of this script's directory (portable: uses $0, not BASH_SOURCE).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

echo "==> docker compose up --build ..."
# --abort-on-container-exit makes `up` return as soon as the one-shot pipeline
# container finishes, so we can open the report right after.
docker compose -f solution/docker-compose.yml up --build --abort-on-container-exit

REPORT="$ROOT/solution/outputs/report.html"
if [ ! -f "$REPORT" ]; then
  echo "ERROR: $REPORT was not produced by the container." >&2
  exit 1
fi

echo "==> Opening $REPORT"
case "$(uname -s)" in
  Darwin) open "$REPORT" ;;
  Linux)  xdg-open "$REPORT" >/dev/null 2>&1 || echo "Open it manually: $REPORT" ;;
  MINGW*|MSYS*|CYGWIN*) start "" "$REPORT" ;;
  *)      echo "Open it manually: $REPORT" ;;
esac
echo "==> Done."
