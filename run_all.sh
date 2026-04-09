#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-/dev/ttyUSB0}"
BAUD="${2:-115200}"

cd "$PROJECT_DIR"

echo "[1/4] Closing conflicting processes..."
# Stop prior local viewers launched from this project
pkill -f "pc_rotation_viewer.py" 2>/dev/null || true
# Stop PlatformIO monitor processes if present
pkill -f "platformio.*device monitor" 2>/dev/null || true
pkill -f "miniterm.py.*$PORT" 2>/dev/null || true

# Kill any process currently holding the serial port
if command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -t "$PORT" 2>/dev/null || true)"
  if [[ -n "$PIDS" ]]; then
    echo "Killing processes on $PORT: $PIDS"
    kill $PIDS 2>/dev/null || true
    sleep 0.5
  fi
fi

if [[ ! -e "$PORT" ]]; then
  echo "Error: serial port $PORT not found."
  echo "Try: ls /dev/ttyUSB* /dev/ttyACM*"
  exit 1
fi

echo "[2/4] Building and uploading firmware..."
pio run -t upload --upload-port "$PORT"

echo "[3/4] Starting desktop rotation window..."
if ! python3 -c "import serial" >/dev/null 2>&1; then
  echo "pyserial not found. Installing for user..."
  python3 -m pip install --user pyserial
fi

echo "[4/4] Running viewer on $PORT @ $BAUD"
exec python3 "$PROJECT_DIR/pc_rotation_viewer.py" --port "$PORT" --baud "$BAUD"
