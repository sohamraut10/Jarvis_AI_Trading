#!/data/data/com.termux/files/usr/bin/bash
# ── JARVIS AI Trading — Termux stop script ────────────────────────────────────
# Usage:  bash scripts/stop_termux.sh

PID_FILE="$(dirname "$0")/../logs/jarvis.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "[JARVIS] stopped server PID $PID"
    else
        echo "[JARVIS] PID $PID not running"
    fi
    rm -f "$PID_FILE"
else
    # fallback — pkill by module name
    pkill -f "termux_server" 2>/dev/null \
        && echo "[JARVIS] stopped via pkill" \
        || echo "[JARVIS] no running server found"
fi

# Release wake lock if held
if command -v termux-wake-unlock &>/dev/null; then
    termux-wake-unlock
    echo "[JARVIS] wake lock released"
fi

echo "[JARVIS] done"
