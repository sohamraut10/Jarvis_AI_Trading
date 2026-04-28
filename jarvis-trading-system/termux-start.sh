#!/data/data/com.termux/files/usr/bin/bash
# JARVIS Termux launcher — starts backend + frontend in split panes

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

# Ensure data/logs dirs exist
mkdir -p data logs

# Create default settings.json if missing
if [ ! -f data/settings.json ]; then
  echo '{"paper_mode":true,"initial_capital":10000,"kill_switch_pct":0.03}' > data/settings.json
fi

# Detect local IP for display
LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/{print $7}' || hostname -I 2>/dev/null | awk '{print $1}')

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║      JARVIS  Trading  Command        ║"
echo "  ╚══════════════════════════════════════╝"
echo ""
echo "  Starting backend  (port 8765)…"

# Start backend in background, log to file
python -m uvicorn server.ws_server:app \
  --host 0.0.0.0 --port 8765 \
  --log-level warning \
  > logs/server.log 2>&1 &
BACKEND_PID=$!
echo "  Backend PID: $BACKEND_PID"

# Wait for backend to be ready (up to 15s)
for i in $(seq 1 15); do
  sleep 1
  if curl -sf http://127.0.0.1:8765/api/status > /dev/null 2>&1; then
    echo "  Backend ready ✓"
    break
  fi
  if [ $i -eq 15 ]; then
    echo "  WARNING: backend did not respond after 15s — check logs/server.log"
  fi
done

echo ""
echo "  Starting dashboard (port 5173)…"
cd dashboard
npm run dev -- --host 0.0.0.0 &
VITE_PID=$!
echo "  Vite PID: $VITE_PID"

echo ""
echo "  ────────────────────────────────────────"
echo "  Open in Chrome:  http://localhost:5173"
if [ -n "$LOCAL_IP" ]; then
echo "  From another device: http://$LOCAL_IP:5173"
fi
echo "  ────────────────────────────────────────"
echo "  Press Ctrl-C to stop both servers"
echo ""

# Trap Ctrl-C — kill both child processes
trap "echo ''; echo 'Stopping JARVIS…'; kill $BACKEND_PID $VITE_PID 2>/dev/null; exit 0" INT TERM

# Keep script alive while children run
wait
