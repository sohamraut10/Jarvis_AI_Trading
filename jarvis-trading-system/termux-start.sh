#!/data/data/com.termux/files/usr/bin/bash
# JARVIS Termux launcher

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

# ── Smoke-test imports before daemonising ─────────────────────────────────────
echo "  Checking dependencies…"
python - <<'PYCHECK'
import sys
missing = []
for mod in ["fastapi", "uvicorn", "pydantic_settings", "aiosqlite", "apscheduler", "numpy"]:
    try:
        __import__(mod)
    except ImportError:
        missing.append(mod)
if missing:
    print(f"  MISSING: {', '.join(missing)}")
    print("  Run: pip install -r requirements-termux.txt")
    sys.exit(1)
print("  Core deps OK")
PYCHECK

# ── Start backend ─────────────────────────────────────────────────────────────
echo "  Starting backend (port 8765)…"
python -m uvicorn server.ws_server:app \
  --host 0.0.0.0 --port 8765 \
  --log-level info \
  > logs/server.log 2>&1 &
BACKEND_PID=$!

# Wait up to 20s for backend to become ready
READY=0
for i in $(seq 1 20); do
  sleep 1
  if curl -sf http://127.0.0.1:8765/api/status > /dev/null 2>&1; then
    READY=1
    echo "  Backend ready ✓"
    break
  fi
done

if [ $READY -eq 0 ]; then
  echo ""
  echo "  ✗ Backend failed to start. Last error:"
  echo "  ────────────────────────────────────────"
  tail -20 logs/server.log
  echo "  ────────────────────────────────────────"
  echo "  To debug: python -m uvicorn server.ws_server:app --port 8765"
  kill $BACKEND_PID 2>/dev/null
  exit 1
fi

# ── Start frontend ────────────────────────────────────────────────────────────
echo "  Starting dashboard (port 5173)…"
cd dashboard
npm run dev -- --host 0.0.0.0 &
VITE_PID=$!
cd "$REPO_DIR"
sleep 3

LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/{print $7}' \
           || hostname -I 2>/dev/null | awk '{print $1}')

echo ""
echo "  ────────────────────────────────────────"
echo "  Open in Chrome:  http://localhost:5173"
[ -n "$LOCAL_IP" ] && echo "  From PC/tablet:  http://$LOCAL_IP:5173"
echo "  ────────────────────────────────────────"
echo "  Ctrl-C to stop"
echo ""

trap "echo 'Stopping…'; kill $BACKEND_PID $VITE_PID 2>/dev/null; exit 0" INT TERM
wait
