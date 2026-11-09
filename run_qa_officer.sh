#!/usr/bin/env bash
# QA Officer - Startup Script
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "============================================="
echo "       🚀 Starting QA Officer Assistant      "
echo "============================================="

# Ensure storage directories
mkdir -p "$DIR/backend/storage/evidence"

# Set Python path to local workspace packages and user packages
export PYTHONPATH="$DIR/backend/site-packages:$DIR/backend:$PYTHONPATH"
export PATH="$HOME/.local/bin:$PATH"

echo "1. Starting FastAPI Backend on http://localhost:8000..."
cd "$DIR/backend"
python3 main.py &
BACKEND_PID=$!

echo "2. Starting Vite Frontend on http://localhost:5173..."
cd "$DIR/frontend"
npm run dev -- --host 0.0.0.0 &
FRONTEND_PID=$!

trap "echo 'Shutting down QA Officer...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true" EXIT INT TERM

echo ""
echo "✅ QA Officer is RUNNING!"
echo "   • Frontend: http://localhost:5173"
echo "   • Backend API: http://localhost:8000"
echo "   • API Docs (Swagger): http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop all services."

wait
