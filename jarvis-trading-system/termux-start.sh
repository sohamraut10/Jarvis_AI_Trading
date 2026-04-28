#!/data/data/com.termux/files/usr/bin/bash
# JARVIS Termux launcher — uses pure-Python server (no FastAPI/pydantic)

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

mkdir -p data logs

if [ ! -f data/settings.json ]; then
  echo '{"paper_mode":true,"initial_capital":10000,"kill_switch_pct":0.03}' > data/settings.json
fi

echo ""
echo "  JARVIS Trading Command Center"
echo "  ─────────────────────────────"

# ── Smoke-test: only need numpy + websockets ──────────────────────────────────
echo "  Checking dependencies…"
python - <<'PYCHECK'
import sys
missing = []
for mod in ["numpy", "websockets", "aiosqlite", "apscheduler"]:
    try:
        __import__(mod)
    except ImportError:
        missing.append(mod)
if missing:
    print(f"  MISSING: {', '.join(missing)}")
    print("  Run:  pkg install python-numpy")
    print("        pip install -r requirements-termux.txt")
    sys.exit(1)
print("  Deps OK")
PYCHECK

# ── Start backend ─────────────────────────────────────────────────────────────
echo "  Starting backend  (WS:8765  HTTP:8766)…"
python -m server.termux_server > logs/server.log 2>&1 &
BACKEND_PID=$!

# Wait up to 20s for HTTP API to respond
READY=0
for i in $(seq 1 20); do
  sleep 1
  if curl -sf http://127.0.0.1:8766/api/status > /dev/null 2>&1; then
    READY=1
    echo "  Backend ready ✓"
    break
  fi
done

if [ $READY -eq 0 ]; then
  echo ""
  echo "  ✗ Backend failed. Last error:"
  echo "  ────────────────────────────────────────"
  tail -20 logs/server.log
  echo "  ────────────────────────────────────────"
  echo "  Debug: python -m server.termux_server"
  kill $BACKEND_PID 2>/dev/null
  exit 1
fi

# ── Start frontend (Termux Vite config: API→8766) ─────────────────────────────
echo "  Starting dashboard (port 5173)…"
cd dashboard
npm run dev -- --host 0.0.0.0 --config vite.termux.config.js &
VITE_PID=$!
cd "$REPO_DIR"
sleep 3

LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/{print $7}' \
           || hostname -I 2>/dev/null | awk '{print $1}')

echo ""
echo "  ────────────────────────────────────────"
echo "  Open in Chrome:  http://localhost:5173"
[ -n "$LOCAL_IP" ] && echo "  From another device: http://$LOCAL_IP:5173"
echo "  ────────────────────────────────────────"
echo "  Ctrl-C to stop"
echo ""

trap "echo 'Stopping…'; kill $BACKEND_PID $VITE_PID 2>/dev/null; exit 0" INT TERM
wait
