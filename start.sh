#!/bin/bash
# Start Quarterdeck in development mode
cd "$(dirname "$0")"

# Dev binds its own port so it does not fight the installed app for 19418.
# Read from backend/config.py rather than repeated here, so the two cannot drift.
export DECK_DEV=1
source venv/bin/activate
PORT=$(python3 -c 'from backend.config import PORT; print(PORT)')

# Backend
# --reload-dir is not optional here. watchfiles is not installed, so uvicorn
# falls back to StatReload, which stats every .py file it can find under the
# working directory four times a second. From the repo root that is 3344 files
# — venv/, build/, dist/, archived docs — about 13k stats a second, forever.
# It burned 40-60% of a core for five days straight and made the whole machine
# feel slow, endpoint-security software amplifying every one of those stats.
# The backend package is 28 files, and it is the only thing that changes.
uvicorn backend.api:app --reload --reload-dir backend --port "$PORT" &
BACKEND_PID=$!

# Frontend
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

echo "🟢 Quarterdeck running (dev, port $PORT)"
echo "   Backend:  http://127.0.0.1:$PORT/docs"
echo "   Frontend: http://127.0.0.1:5173"
echo ""
echo "Press Ctrl+C to stop"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
