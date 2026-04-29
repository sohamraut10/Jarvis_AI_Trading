#!/data/data/com.termux/files/usr/bin/bash
# ── JARVIS AI Trading — Termux startup script ─────────────────────────────────
# Usage:  bash scripts/start_termux.sh
# Keeps screen on, runs server in background, tails logs.

set -e
cd "$(dirname "$0")/.."   # project root

# ── Termux wake lock (keeps CPU alive while screen is off) ────────────────────
if command -v termux-wake-lock &>/dev/null; then
    termux-wake-lock
    echo "[JARVIS] wake lock acquired"
else
    echo "[JARVIS] termux-wake-lock not available — install termux-api for battery savings"
fi

# ── Environment ───────────────────────────────────────────────────────────────
export TERMUX_MODE=true
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

# Load .env if present
[ -f .env ] && export $(grep -v '^#' .env | xargs)

# ── Directories ───────────────────────────────────────────────────────────────
mkdir -p logs data

# ── Kill any existing server ─────────────────────────────────────────────────
pkill -f "termux_server" 2>/dev/null && echo "[JARVIS] killed previous instance" || true
sleep 1

# ── Start server ──────────────────────────────────────────────────────────────
LOG=logs/jarvis.log
echo "[JARVIS] starting server — log: $LOG"
nohup python -m server.termux_server >> "$LOG" 2>&1 &
SERVER_PID=$!
echo $SERVER_PID > logs/jarvis.pid
echo "[JARVIS] server PID $SERVER_PID"

# ── Wait for server to be ready ───────────────────────────────────────────────
echo -n "[JARVIS] waiting for HTTP port 8766 "
for i in $(seq 1 30); do
    if curl -s http://localhost:8766/api/status > /dev/null 2>&1; then
        echo " ready"
        break
    fi
    echo -n "."
    sleep 1
done

echo ""
echo "[JARVIS] ✓ server running"
echo "[JARVIS]   Dashboard : http://localhost:5173  (run: cd dashboard && npm run dev)"
echo "[JARVIS]   API       : http://localhost:8766/api/status"
echo "[JARVIS]   Logs      : tail -f $LOG"
echo ""
echo "[JARVIS] tailing logs (Ctrl+C to stop tailing — server keeps running)"
tail -f "$LOG"
