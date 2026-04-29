#!/bin/bash
# ── JARVIS AI Trading — Local machine startup script ──────────────────────────
# Usage:  bash scripts/start_local.sh [--no-dashboard]
#
# Requires: Python 3.10+, Node.js 18+
# Data:     yfinance (free, ~15s delay) — no broker account needed

set -e
cd "$(dirname "$0")/.."   # project root

START_DASHBOARD=true
[ "$1" = "--no-dashboard" ] && START_DASHBOARD=false

# ── Environment ───────────────────────────────────────────────────────────────
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
[ -f .env ] && export $(grep -v '^#' .env | xargs)

mkdir -p logs data

# ── Ensure yfinance is installed ──────────────────────────────────────────────
if ! python -c "import yfinance" 2>/dev/null; then
    echo "[JARVIS] installing yfinance..."
    pip install yfinance --quiet
fi

# ── Kill any existing processes ───────────────────────────────────────────────
pkill -f "termux_server" 2>/dev/null && echo "[JARVIS] killed previous server" || true
pkill -f "vite"          2>/dev/null && echo "[JARVIS] killed previous dashboard" || true
sleep 1

# ── Start backend server ──────────────────────────────────────────────────────
LOG=logs/jarvis.log
echo "[JARVIS] starting server (yfinance forex feed)..."
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
    DASH=dashboard
    if [ ! -d "$DASH/node_modules" ]; then
        echo "[JARVIS] installing dashboard npm packages (first run)..."
        ( cd "$DASH" && npm install 2>&1 | tail -5 )
    fi

    echo "[JARVIS] starting dashboard..."
    DASH_LOG=logs/dashboard.log
    nohup sh -c "cd $DASH && npm run dev" >> "$DASH_LOG" 2>&1 &
    echo $! > logs/dashboard.pid

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
echo "[JARVIS] all services running"
echo "[JARVIS]   Dashboard : http://localhost:5173"
echo "[JARVIS]   API       : http://localhost:8766/api/status"
echo "[JARVIS]   Feed      : Yahoo Finance (EUR/USD, GBP/USD, USD/JPY, ...)"
echo "[JARVIS]   Logs      : tail -f logs/jarvis.log"
echo ""
echo "[JARVIS] tailing server log (Ctrl+C stops tailing — services keep running)"
tail -f "$LOG"
