#!/data/data/com.termux/files/usr/bin/bash
# ── JARVIS AI Trading — Termux dependency installer ───────────────────────────
# Usage:  bash scripts/install_termux.sh

set -e
cd "$(dirname "$0")/.."   # project root

echo "[JARVIS] updating Termux package lists..."
pkg update -y 2>/dev/null || true

# ── Step 1: pkg for Termux-native binaries ────────────────────────────────────
echo ""
echo "[JARVIS] step 1: pkg install (numpy, scipy)..."
pkg install -y python-numpy 2>/dev/null && echo "  ✓ numpy" || echo "  ! numpy skipped"
pkg install -y python-scipy 2>/dev/null && echo "  ✓ scipy" || echo "  ! scipy skipped"

# ── Step 2: pydantic (root dep for dhanhq + anthropic) ───────────────────────
echo ""
echo "[JARVIS] step 2: pydantic (needs Rust — compiling pydantic-core)..."
echo "         This takes 10-20 min on first run. Please wait..."
pkg install -y rust 2>/dev/null || true
pip install "pydantic>=2.9.0"
echo "  ✓ pydantic"

# ── Step 3: optional compiled packages ───────────────────────────────────────
echo ""
echo "[JARVIS] step 3: optional ML packages (binary-only, skipped if unavailable)..."
pip install -q --only-binary :all: "pandas>=2.2.3"    2>/dev/null && echo "  ✓ pandas"    || echo "  - pandas skipped"
pip install -q --only-binary :all: "scikit-learn>=1.5.0" 2>/dev/null && echo "  ✓ sklearn"   || echo "  - scikit-learn skipped"
pip install -q --only-binary :all: "hmmlearn>=0.3.2"  2>/dev/null && echo "  ✓ hmmlearn"  || echo "  - hmmlearn skipped"

# ── Step 4: required pure-Python packages ─────────────────────────────────────
echo ""
echo "[JARVIS] step 4: required packages..."
pip install -q \
    "websockets>=12.0" \
    "httpx>=0.27.0" \
    "python-dotenv>=1.0.1" \
    "dhanhq==2.0.1" \
    "aiofiles>=23.2.1" \
    "sqlalchemy>=2.0.36" \
    "aiosqlite>=0.20.0" \
    "pyyaml>=6.0.1"
echo "  ✓ core packages"

# ── Step 5: LLM SDKs (anthropic required; openai + google optional) ───────────
echo ""
echo "[JARVIS] step 5: LLM SDKs..."
pip install -q "anthropic>=0.28.0" && echo "  ✓ anthropic" || echo "  ! anthropic failed"
pip install -q --only-binary :all: "openai>=1.30.0" 2>/dev/null && echo "  ✓ openai" || echo "  - openai skipped"
# google-generativeai requires grpcio (C++ build) — skip on Termux
echo "  - google-generativeai skipped (grpcio C++ dep — Gemini uses REST fallback)"

echo ""
echo "[JARVIS] ✓ done — run: bash scripts/start_termux.sh"
