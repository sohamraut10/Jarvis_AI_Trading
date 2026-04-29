#!/data/data/com.termux/files/usr/bin/bash
# ── JARVIS AI Trading — Termux dependency installer ───────────────────────────
# Usage:  bash scripts/install_termux.sh

set -e
cd "$(dirname "$0")/.."   # project root

# Helper: install one package, binary-only first, then allow source if needed
p() {
    local spec="$1"
    local name="${spec%%[>=!<=]*}"
    printf "  %-32s" "$name"
    # Try binary wheel first (fast, no compilation)
    if pip install -q --only-binary :all: "$spec" 2>/dev/null; then
        echo "✓"
        return
    fi
    # Fall back to source only for pure-Python packages (no C/Rust extensions)
    if pip install -q --no-build-isolation "$spec" 2>/dev/null; then
        echo "✓"
    else
        echo "FAILED"
    fi
}

echo "[JARVIS] updating Termux package lists..."
pkg update -y 2>/dev/null || true

# ── Step 1: pkg for Termux-native binaries ────────────────────────────────────
echo ""
echo "[JARVIS] step 1: pkg install (numpy, scipy)..."
pkg install -y python-numpy 2>/dev/null && echo "  ✓ numpy" || echo "  ! numpy skipped"
pkg install -y python-scipy 2>/dev/null && echo "  ✓ scipy" || echo "  ! scipy skipped"

# ── Step 2: pydantic ──────────────────────────────────────────────────────────
echo ""
echo "[JARVIS] step 2: pydantic..."
printf "  %-32s" "pydantic"
if pip install -q --only-binary :all: "pydantic>=2.9.0" 2>/dev/null; then
    echo "✓ (binary)"
else
    echo "compiling (Rust, ~15 min)..."
    pkg install -y rust 2>/dev/null || true
    CARGO_BUILD_JOBS=1 CARGO_PROFILE_RELEASE_OPT_LEVEL=1 pip install "pydantic>=2.9.0"
fi

# ── Step 3: optional ML packages ─────────────────────────────────────────────
echo ""
echo "[JARVIS] step 3: optional ML packages..."
pip install -q --only-binary :all: "pandas>=2.2.3"       2>/dev/null && echo "  ✓ pandas"       || echo "  - pandas skipped"
pip install -q --only-binary :all: "scikit-learn>=1.5.0" 2>/dev/null && echo "  ✓ scikit-learn" || echo "  - scikit-learn skipped"
pip install -q --only-binary :all: "hmmlearn>=0.3.2"     2>/dev/null && echo "  ✓ hmmlearn"     || echo "  - hmmlearn skipped"

# ── Step 4: required packages (one-by-one so failures are visible) ────────────
echo ""
echo "[JARVIS] step 4: required packages..."
p "websockets>=12.0"
p "httpx>=0.27.0"
p "python-dotenv>=1.0.1"
p "dhanhq==2.0.1"
p "aiofiles>=23.2.1"
p "sqlalchemy>=2.0.36"
p "aiosqlite>=0.20.0"
p "pyyaml>=6.0.1"

# ── Step 5: LLM SDKs ──────────────────────────────────────────────────────────
echo ""
echo "[JARVIS] step 5: LLM SDKs..."
p "anthropic>=0.28.0"
p "openai>=1.30.0"
echo "  - google-generativeai skipped (grpcio C++ dep — Gemini unavailable on Termux)"

echo ""
echo "[JARVIS] ✓ done — run: bash scripts/start_termux.sh"
