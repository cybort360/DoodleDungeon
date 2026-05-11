#!/bin/bash
set -e

echo ""
echo "  ⚔️  DoodleDungeon — startup"
echo "  ───────────────────────────────────────────────"
echo ""

# Check Ollama
if ! command -v ollama &> /dev/null; then
  echo "  ❌  Ollama not found."
  echo "      Install it from https://ollama.com then rerun this script."
  exit 1
fi

echo "  ✅  Ollama found"
echo ""
echo "  Pulling Gemma 4 models (skip if already downloaded)..."
echo ""
ollama pull gemma4:e4b
ollama pull gemma4:26b

echo ""
echo "  Installing Python dependencies..."
cd "$(dirname "$0")/backend"
pip3 install -r requirements.txt -q

# Generate self-signed TLS certificate for HTTPS (required for iPhone camera)
if [ ! -f cert.pem ]; then
  echo ""
  echo "  🔐  Generating self-signed certificate for HTTPS..."
  openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem \
    -days 365 -nodes -subj '/CN=BodyQuest' 2>/dev/null
  echo "  ✅  Certificate created"
fi

# Get local IP for display
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo "YOUR_IP")

echo ""
echo "  ───────────────────────────────────────────────"
echo "  🌍  Starting DoodleDungeon at https://localhost:8000"
echo ""
echo "  iPhone users:"
echo "    1. Open Safari on iPhone: https://${LOCAL_IP}:8000"
echo "    2. Tap 'Show Details' → 'visit this website' → 'Visit Website'"
echo "       (one-time security warning for the self-signed cert)"
echo "    3. Tap ⚙️ Settings in the app and set URL to https://${LOCAL_IP}:8000"
echo "  ───────────────────────────────────────────────"
echo ""

SSL_KEYFILE=key.pem SSL_CERTFILE=cert.pem python3 main.py
