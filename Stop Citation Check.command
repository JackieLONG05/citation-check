#!/bin/bash

set -u

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$ROOT_DIR/.run"

echo "Stopping Citation Check..."

stop_pid_file() {
  local file="$1"
  local label="$2"
  if [ -f "$file" ]; then
    local pid
    pid="$(cat "$file")"
    if kill -0 "$pid" >/dev/null 2>&1; then
      echo "Stopping $label process $pid..."
      kill "$pid" >/dev/null 2>&1 || true
    fi
    rm -f "$file"
  fi
}

stop_port() {
  local port="$1"
  local label="$2"
  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "Stopping $label on port $port..."
    kill $pids >/dev/null 2>&1 || true
  fi
}

stop_pid_file "$PID_DIR/backend.pid" "backend"
stop_pid_file "$PID_DIR/frontend.pid" "frontend"
stop_port 8000 "backend"
stop_port 5173 "frontend"

echo "Done."
echo
read -r -p "Press Enter to close this window."
