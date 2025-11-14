#!/bin/bash

# Restart Server Script
# This will kill any process on port 8000 and start fresh

echo "🔍 Checking for processes on port 8000..."
PID=$(lsof -ti:8000)

if [ ! -z "$PID" ]; then
    echo "⚠️  Found process $PID on port 8000"
    echo "🔪 Killing process..."
    kill -9 $PID
    sleep 2
    echo "✅ Process killed"
else
    echo "✅ Port 8000 is free"
fi

echo "📂 Navigating to project directory..."
cd /home/eugene/crm-backend

echo "🔄 Pulling latest code..."
git pull origin main

echo "🚀 Starting server..."
.venv/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 2>&1 | tee logs/server.log
