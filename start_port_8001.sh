#!/bin/bash

# Alternative Server Start Script (Port 8001)
# Use this if port 8000 is stuck

echo "🚀 Starting server on PORT 8001..."
cd /home/eugene/crm-backend

echo "🔄 Pulling latest code..."
git pull origin main

echo "✅ Starting server on port 8001..."
.venv/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8001 2>&1 | tee logs/server.log

echo ""
echo "⚠️  IMPORTANT: Update your Nginx/Apache config to proxy to port 8001 instead of 8000"
