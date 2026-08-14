#!/bin/bash
# Start Quarterdeck in development mode
cd "$(dirname "$0")"

# Dev binds its own port so it does not fight the installed app for 19418.
# Read from backend/config.py rather than repeated here, so the two cannot drift.
export DECK_DEV=1
source venv/bin/activate
PORT=$(python3 -c 'from backend.config import PORT; print(PORT)')

# Backend
uvicorn backend.api:app --reload --port "$PORT" &
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
