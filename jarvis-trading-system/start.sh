#!/usr/bin/env bash
# Start JARVIS locally: Python backend + Vite dashboard
set -e
cd "$(dirname "$0")"

mkdir -p data logs

# Kill any previous instances on our ports
for PORT in 8765 8766 5173; do
  lsof -ti tcp:$PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
done

echo "Starting backend..."
python -m server.termux_server &
BACKEND_PID=$!

echo "Starting dashboard (http://localhost:5173)..."
cd dashboard
npm run dev &
DASH_PID=$!

trap "kill $BACKEND_PID $DASH_PID 2>/dev/null; exit" INT TERM

echo ""
echo "  Backend : ws://localhost:8765  http://localhost:8766"
echo "  Frontend: http://localhost:5173"
echo ""
echo "Press Ctrl+C to stop."
wait
