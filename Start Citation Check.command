#!/bin/bash

set -u

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/logs"
PID_DIR="$ROOT_DIR/.run"
BACKEND_URL="http://127.0.0.1:8000"
FRONTEND_URL="http://127.0.0.1:5173"

mkdir -p "$LOG_DIR" "$PID_DIR"
cd "$ROOT_DIR" || exit 1

echo "Starting Citation Check..."
echo "Project folder: $ROOT_DIR"
echo

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1"
    echo "$2"
    echo
    read -r -p "Press Enter to close this window."
    exit 1
  fi
}

require_command python3 "Install Python 3 first: https://www.python.org/downloads/"
require_command npm "Install Node.js first: https://nodejs.org/"

port_in_use() {
  lsof -tiTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

wait_for_url() {
  local url="$1"
  local name="$2"
  local tries=45
  local i=0
  while [ "$i" -lt "$tries" ]; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "$name is ready: $url"
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  echo "$name did not become ready in time. Check logs in: $LOG_DIR"
  return 1
}

echo "Preparing backend..."
if [ ! -x "$BACKEND_DIR/.venv/bin/python" ]; then
  python3 -m venv "$BACKEND_DIR/.venv" || exit 1
fi

REQ_HASH="$(shasum -a 256 "$BACKEND_DIR/requirements.txt" | awk '{print $1}')"
REQ_STAMP="$BACKEND_DIR/.venv/.requirements.sha256"
if [ ! -f "$REQ_STAMP" ] || [ "$(cat "$REQ_STAMP")" != "$REQ_HASH" ]; then
  "$BACKEND_DIR/.venv/bin/python" -m pip install --upgrade pip
  "$BACKEND_DIR/.venv/bin/python" -m pip install -r "$BACKEND_DIR/requirements.txt" || exit 1
  echo "$REQ_HASH" > "$REQ_STAMP"
else
  echo "Backend dependencies are already installed."
fi

echo
echo "Preparing frontend..."
PKG_HASH="$(shasum -a 256 "$FRONTEND_DIR/package-lock.json" | awk '{print $1}')"
PKG_STAMP="$FRONTEND_DIR/node_modules/.package-lock.sha256"
if [ ! -d "$FRONTEND_DIR/node_modules" ] || [ ! -f "$PKG_STAMP" ] || [ "$(cat "$PKG_STAMP")" != "$PKG_HASH" ]; then
  (cd "$FRONTEND_DIR" && npm install) || exit 1
  echo "$PKG_HASH" > "$PKG_STAMP"
else
  echo "Frontend dependencies are already installed."
fi

echo
echo "Starting backend..."
if port_in_use 8000; then
  if curl -fsS "$BACKEND_URL/health" >/dev/null 2>&1; then
    echo "Backend is already running."
  else
    echo "Port 8000 is already in use by another process."
    echo "Run Stop Citation Check.command, or close the process using port 8000."
    read -r -p "Press Enter to close this window."
    exit 1
  fi
else
  (
    cd "$BACKEND_DIR" || exit 1
    source .venv/bin/activate
    PYTHONPATH=. uvicorn app.main:app --host 127.0.0.1 --port 8000
  ) > "$LOG_DIR/backend.log" 2>&1 &
  echo $! > "$PID_DIR/backend.pid"
fi

echo "Starting frontend..."
if port_in_use 5173; then
  if curl -fsSI "$FRONTEND_URL" >/dev/null 2>&1; then
    echo "Frontend is already running."
  else
    echo "Port 5173 is already in use by another process."
    echo "Run Stop Citation Check.command, or close the process using port 5173."
    read -r -p "Press Enter to close this window."
    exit 1
  fi
else
  (
    cd "$FRONTEND_DIR" || exit 1
    npm run dev -- --host 127.0.0.1 --port 5173
  ) > "$LOG_DIR/frontend.log" 2>&1 &
  echo $! > "$PID_DIR/frontend.pid"
fi

echo
wait_for_url "$BACKEND_URL/health" "Backend" || {
  tail -n 40 "$LOG_DIR/backend.log"
  read -r -p "Press Enter to close this window."
  exit 1
}
wait_for_url "$FRONTEND_URL" "Frontend" || {
  tail -n 40 "$LOG_DIR/frontend.log"
  read -r -p "Press Enter to close this window."
  exit 1
}

echo
echo "Opening Citation Check..."
open "$FRONTEND_URL"
echo
echo "Citation Check is running."
echo "Keep this Terminal window open while using the app, or use Stop Citation Check.command when finished."
echo
read -r -p "Press Enter to close this window. The app will keep running in the background."
