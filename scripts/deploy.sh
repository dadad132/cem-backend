#!/bin/bash
# Quick deployment script - run this after uploading to Ubuntu

echo "🚀 CRM Backend Quick Deployment"
echo "================================"
echo ""

# Make scripts executable
chmod +x setup_ubuntu.sh
chmod +x start_ubuntu.sh
chmod +x start_server.py

# Run setup
./setup_ubuntu.sh

echo ""
echo "✅ Deployment complete!"
echo ""
echo "Run the server with: ./start_ubuntu.sh"
