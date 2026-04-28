#!/data/data/com.termux/files/usr/bin/bash
# JARVIS Termux launcher
# Backend runs in FOREGROUND so all engine logs print to this terminal.
# Vite runs in background; its logs go to /tmp/jarvis_vite.log

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

mkdir -p data logs

if [ ! -f data/settings.json ]; then
  echo '{"paper_mode":true,"initial_capital":10000,"kill_switch_pct":0.03}' > data/settings.json
fi

# ── Smoke-test ────────────────────────────────────────────────────────────────
echo ""
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

# ── Start Vite in background ──────────────────────────────────────────────────
echo "  Starting dashboard (port 5173)…"
cd dashboard
npm run dev -- --host 0.0.0.0 --config vite.termux.config.js \
  > /tmp/jarvis_vite.log 2>&1 &
VITE_PID=$!
cd "$REPO_DIR"

# Wait for Vite to be ready (up to 15s)
for i in $(seq 1 15); do
  sleep 1
  if grep -q "Local:" /tmp/jarvis_vite.log 2>/dev/null; then
    break
  fi
done

LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/{print $7}' \
           || hostname -I 2>/dev/null | awk '{print $1}')

echo ""
echo "  ────────────────────────────────────────────"
echo "  Dashboard : http://localhost:5173"
[ -n "$LOCAL_IP" ] && echo "  From PC   : http://$LOCAL_IP:5173"
echo "  Vite logs : tail -f /tmp/jarvis_vite.log"
echo "  ────────────────────────────────────────────"
echo "  Starting backend — engine logs appear below"
echo "  Press Ctrl-C to stop everything"
echo ""

# Kill Vite when the backend exits (Ctrl-C or crash)
trap "echo ''; echo 'Stopping…'; kill $VITE_PID 2>/dev/null; exit 0" INT TERM EXIT

# ── Run backend in FOREGROUND (logs print here) ───────────────────────────────
python -m server.termux_server
