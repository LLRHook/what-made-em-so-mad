#!/usr/bin/env bash
# Launches the backend (uvicorn) and the frontend (vite) in parallel.
# Ctrl+C stops both.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend_dir="$repo_root/backend"
front_dir="$repo_root/frontend"

if [ ! -f "$backend_dir/pyproject.toml" ]; then
  echo "Cannot find backend/pyproject.toml at $backend_dir" >&2
  exit 1
fi
if [ ! -f "$front_dir/package.json" ]; then
  echo "Cannot find frontend/package.json at $front_dir" >&2
  exit 1
fi

if [ ! -d "$front_dir/node_modules" ]; then
  echo "[dev] frontend/node_modules not found. Running npm install first..."
  (cd "$front_dir" && npm install)
fi

pick_port() {
  python - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind(("127.0.0.1", 0))
    print(s.getsockname()[1])
PY
}

backend_port="${WMESM_BACKEND_PORT:-$(pick_port)}"
frontend_port="${WMESM_FRONTEND_PORT:-$(pick_port)}"

pids=()
trap 'echo "[dev] stopping..."; kill "${pids[@]}" 2>/dev/null || true; wait || true' INT TERM EXIT

echo "[dev] Starting backend on http://localhost:${backend_port}"
(
  cd "$backend_dir"
  if [ -f ".venv/bin/activate" ]; then
    # shellcheck source=/dev/null
    . ".venv/bin/activate"
  fi
  export WMESM_BACKEND_PORT="$backend_port"
  exec uvicorn app.main:app --reload --port "$backend_port"
) &
pids+=("$!")

echo "[dev] Starting frontend on http://localhost:${frontend_port}"
(
  cd "$front_dir"
  export WMESM_BACKEND_PORT="$backend_port"
  export WMESM_FRONTEND_PORT="$frontend_port"
  exec npm run dev -- --host localhost
) &
pids+=("$!")

wait
