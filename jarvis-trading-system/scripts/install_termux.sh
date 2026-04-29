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
echo "[JARVIS] step 2: pydantic..."

# First try: grab the pre-built manylinux aarch64 wheel and force-install it
# (avoids Rust compilation entirely — works because Android kernel is Linux aarch64)
echo "  trying pre-built Linux aarch64 wheel (no compilation)..."
mkdir -p "${TMPDIR:-$HOME/.tmp}/jarvis_wheels"
if pip download \
        --only-binary :all: \
        --platform manylinux_2_17_aarch64 \
        --python-version 313 \
        --implementation cp \
        --abi cp313 \
        -d ${TMPDIR:-$HOME/.tmp}/jarvis_wheels \
        "pydantic>=2.9.0" -q 2>/dev/null \
   && pip install --no-deps ${TMPDIR:-$HOME/.tmp}/jarvis_wheels/pydantic_core-*.whl 2>/dev/null \
   && pip install --no-deps ${TMPDIR:-$HOME/.tmp}/jarvis_wheels/pydantic-*.whl 2>/dev/null; then
    echo "  ✓ pydantic (pre-built wheel)"
else
    # Second try: compile with Rust, single job to avoid OOM on Android
    echo "  no pre-built wheel — compiling with Rust (single-job, ~15 min)..."
    pkg install -y rust 2>/dev/null || true
    CARGO_BUILD_JOBS=1 \
    CARGO_PROFILE_RELEASE_OPT_LEVEL=1 \
    pip install "pydantic>=2.9.0"
    echo "  ✓ pydantic (compiled)"
fi
rm -rf ${TMPDIR:-$HOME/.tmp}/jarvis_wheels

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
