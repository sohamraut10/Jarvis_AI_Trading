#!/data/data/com.termux/files/usr/bin/bash
# JARVIS Termux launcher
# Backend runs in FOREGROUND so all engine logs print to this terminal.
# Vite runs in background; its logs go to $TMPDIR/jarvis_vite.log

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

mkdir -p data logs

if [ ! -f data/settings.json ]; then
  echo '{"paper_mode":true,"initial_capital":10000,"kill_switch_pct":0.03}' > data/settings.json
fi

VITE_LOG="${TMPDIR:-$HOME}/jarvis_vite.log"

# ── Python smoke-test ─────────────────────────────────────────────────────────
echo ""
echo "  Checking Python dependencies…"
python - <<'PYCHECK'
import sys
missing = []
optional_missing = []

required = [
    "websockets", "aiosqlite", "apscheduler", "aiofiles",
    "requests", "dotenv", "sqlalchemy", "yaml",
]
optional = {
    "yfinance":   "pip install yfinance  (needed for forex fallback feed)",
    "anthropic":  "pip install anthropic  (optional: AI brain)",
    "openai":     "pip install openai     (optional: AI brain)",
}

for mod in required:
    try:
        __import__(mod)
    except ImportError:
        missing.append(mod)

for mod, hint in optional.items():
    try:
        __import__(mod)
    except ImportError:
        optional_missing.append(f"  {hint}")

if missing:
    print(f"\n  MISSING REQUIRED: {', '.join(missing)}")
    print("  Run:  pkg install python-numpy")
    print("        pip install -r requirements-termux.txt")
    sys.exit(1)

if optional_missing:
    print("  Optional packages not installed (non-fatal):")
    for m in optional_missing:
        print(m)

print("  Python deps OK")
PYCHECK

# ── Dashboard (Node.js / Vite) ────────────────────────────────────────────────
HAS_NODE=0
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    HAS_NODE=1
fi

if [ "$HAS_NODE" -eq 1 ]; then
    echo "  Starting dashboard (port 5173)…"
    cd dashboard

    # Install node modules if missing
    if [ ! -d node_modules ]; then
        echo "  Installing npm packages (first run)…"
        npm install --silent
    fi

    VITE_CFG="vite.termux.config.js"
    [ ! -f "$VITE_CFG" ] && VITE_CFG="vite.config.js"

    npm run dev -- --host 0.0.0.0 --config "$VITE_CFG" \
      > "$VITE_LOG" 2>&1 &
    VITE_PID=$!
    cd "$REPO_DIR"

    # Wait for Vite to be ready (up to 20s)
    for i in $(seq 1 20); do
      sleep 1
      if grep -q "Local:" "$VITE_LOG" 2>/dev/null; then
        break
      fi
    done

    LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/{print $7}' \
               || hostname -I 2>/dev/null | awk '{print $1}')

    echo ""
    echo "  ────────────────────────────────────────────"
    echo "  Dashboard : http://localhost:5173"
    [ -n "$LOCAL_IP" ] && echo "  From PC   : http://$LOCAL_IP:5173"
    echo "  Vite logs : tail -f $VITE_LOG"
    echo "  ────────────────────────────────────────────"
else
    echo ""
    echo "  ⚠  Node.js not found — dashboard UI will NOT start."
    echo "     To install on Termux:"
    echo "       pkg install nodejs"
    echo "     Then re-run this script."
    echo ""
    echo "  Backend-only mode: connect via WebSocket at ws://localhost:8765"
    echo "  ────────────────────────────────────────────"
    VITE_PID=""
fi

echo "  Starting backend — engine logs appear below"
echo "  Press Ctrl-C to stop everything"
echo ""

# Kill Vite when the backend exits (Ctrl-C or crash)
if [ -n "$VITE_PID" ]; then
    trap "echo ''; echo 'Stopping…'; kill $VITE_PID 2>/dev/null; exit 0" INT TERM EXIT
else
    trap "echo ''; echo 'Stopping…'; exit 0" INT TERM EXIT
fi

# ── Run backend in FOREGROUND (logs print here) ───────────────────────────────
python -m server.termux_server
