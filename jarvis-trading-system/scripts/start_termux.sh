#!/data/data/com.termux/files/usr/bin/bash
# ── JARVIS AI Trading — Termux startup script ─────────────────────────────────
# Usage:  bash scripts/start_termux.sh [--no-dashboard]

set -e
cd "$(dirname "$0")/.."   # project root

START_DASHBOARD=true
[ "$1" = "--no-dashboard" ] && START_DASHBOARD=false

# ── Termux wake lock (keeps CPU alive while screen is off) ────────────────────
if command -v termux-wake-lock &>/dev/null; then
    termux-wake-lock
    echo "[JARVIS] wake lock acquired"
fi

# ── Environment ───────────────────────────────────────────────────────────────
export TERMUX_MODE=true
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
[ -f .env ] && export $(grep -v '^#' .env | xargs)

mkdir -p logs data

# ── Kill any existing processes ───────────────────────────────────────────────
pkill -f "termux_server" 2>/dev/null && echo "[JARVIS] killed previous server" || true
pkill -f "vite"          2>/dev/null && echo "[JARVIS] killed previous dashboard" || true
sleep 1

# ── Start backend server ──────────────────────────────────────────────────────
LOG=logs/jarvis.log
echo "[JARVIS] starting server..."
nohup python -m server.termux_server >> "$LOG" 2>&1 &
echo $! > logs/jarvis.pid

# ── Wait for server ready ─────────────────────────────────────────────────────
echo -n "[JARVIS] waiting for API port 8766 "
for i in $(seq 1 30); do
    if curl -s http://localhost:8766/api/status > /dev/null 2>&1; then
        echo " ready"; break
    fi
    echo -n "."; sleep 1
done

# ── Start dashboard (Node.js) ─────────────────────────────────────────────────
if $START_DASHBOARD; then
    if ! command -v node &>/dev/null; then
        echo ""
        echo "[JARVIS] Node.js not found — installing..."
        pkg install -y nodejs
    fi

    DASH=dashboard
    if [ ! -d "$DASH/node_modules" ]; then
        echo "[JARVIS] installing dashboard npm packages (first run)..."
        ( cd "$DASH" && npm install --prefer-offline 2>&1 | tail -3 )
    fi

    echo "[JARVIS] starting dashboard..."
    DASH_LOG=logs/dashboard.log
    nohup sh -c "cd $DASH && npm run dev -- --config vite.termux.config.js" \
        >> "$DASH_LOG" 2>&1 &
    echo $! > logs/dashboard.pid

    # Wait for Vite
    echo -n "[JARVIS] waiting for dashboard port 5173 "
    for i in $(seq 1 20); do
        if curl -s http://localhost:5173 > /dev/null 2>&1; then
            echo " ready"; break
        fi
        echo -n "."; sleep 1
    done
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "[JARVIS] ✓ all services running"
echo "[JARVIS]   Dashboard : http://localhost:5173"
echo "[JARVIS]   API       : http://localhost:8766/api/status"
echo "[JARVIS]   Logs      : tail -f logs/jarvis.log"
echo ""
echo "[JARVIS] tailing server log (Ctrl+C stops tailing — services keep running)"
tail -f "$LOG"
