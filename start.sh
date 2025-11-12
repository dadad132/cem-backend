#!/bin/bash
# Shell script to start the server with local IP detection

echo "Starting CRM Backend Server..."
echo ""

python3 start_server.py "$@"
