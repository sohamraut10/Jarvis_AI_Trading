#!/data/data/com.termux/files/usr/bin/bash
# ── JARVIS AI Trading — Termux dependency installer ───────────────────────────
# Usage:  bash scripts/install_termux.sh
# Never compiles from source — binary-only everywhere to avoid getting stuck.

set -e
cd "$(dirname "$0")/.."   # project root

echo "[JARVIS] updating Termux package lists..."
pkg update -y 2>/dev/null || true

# ── pkg for Termux-native compiled packages ───────────────────────────────────
echo ""
echo "[JARVIS] step 1: pkg install (numpy, scipy)..."
pkg install -y python-numpy 2>/dev/null && echo "  ✓ numpy" || echo "  ! numpy — will try pip"
pkg install -y python-scipy 2>/dev/null && echo "  ✓ scipy" || echo "  ! scipy — will try pip"

# ── pip binary-only helper (skips instead of compiling) ──────────────────────
pip_bin() {
    local spec="$1"
    local name="${spec%%[>=!<]*}"
    echo -n "  $name ... "
    if pip install -q --only-binary :all: "$spec" 2>/dev/null; then
        echo "✓"
    else
        echo "SKIPPED (no binary wheel — server degrades gracefully)"
    fi
}

echo ""
echo "[JARVIS] step 2: compiled packages via pip (binary wheel or skip)..."
pip_bin "pandas>=2.2.3"
pip_bin "scikit-learn>=1.5.0"
pip_bin "hmmlearn>=0.3.2"    # optional — rule-based fallback exists in code

# ── Pure-Python packages (no compilation ever) ───────────────────────────────
echo ""
echo "[JARVIS] step 3: pure-Python packages..."
pip install -q \
    "websockets==12.0" \
    "httpx==0.27.0" \
    "python-dotenv==1.0.1" \
    "pydantic==2.7.1" \
    "dhanhq==2.0.1" \
    "aiofiles==23.2.1" \
    "sqlalchemy==2.0.30" \
    "aiosqlite==0.20.0" \
    "anthropic>=0.28.0" \
    "openai>=1.30.0" \
    "google-generativeai>=0.8.0" \
    "pyyaml>=6.0.1"
echo "  ✓ all pure-Python packages"

echo ""
echo "[JARVIS] ✓ done — run: bash scripts/start_termux.sh"
