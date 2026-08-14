#!/bin/bash
# Launch Quarterdeck (stable launcher - permissions persist)
cd "$(dirname "$0")"
source venv/bin/activate
exec python3 app.py
