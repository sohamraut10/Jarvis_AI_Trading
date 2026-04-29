#!/data/data/com.termux/files/usr/bin/bash
# ── JARVIS AI Trading — Termux dependency installer ───────────────────────────
# Usage:  bash scripts/install_termux.sh
#
# Strategy:
#   1. pkg install  — Termux-native ARM64 binaries for compiled packages
#   2. pip install  — pure-Python packages only (no C/Cython compilation)

set -e
cd "$(dirname "$0")/.."   # project root

echo "[JARVIS] updating Termux package lists..."
pkg update -y

echo ""
echo "[JARVIS] installing compiled packages via pkg (numpy, pandas, scipy, scikit-learn)..."
pkg install -y \
    python-numpy \
    python-pandas \
    python-scipy \
    python-scikit-learn

echo ""
echo "[JARVIS] installing pure-Python packages via pip..."
pip install --prefer-binary \
    "websockets==12.0" \
    "httpx==0.27.0" \
    "python-dotenv==1.0.1" \
    "pydantic==2.7.1" \
    "dhanhq==2.0.1" \
    "hmmlearn>=0.3.2" \
    "aiofiles==23.2.1" \
    "sqlalchemy==2.0.30" \
    "aiosqlite==0.20.0" \
    "anthropic>=0.28.0" \
    "openai>=1.30.0" \
    "google-generativeai>=0.8.0" \
    "pyyaml>=6.0.1"

echo ""
echo "[JARVIS] ✓ all dependencies installed"
echo "[JARVIS]   run: bash scripts/start_termux.sh"
