#!/data/data/com.termux/files/usr/bin/bash
# ── JARVIS AI Trading — Termux dependency installer ───────────────────────────
# Usage:  bash scripts/install_termux.sh
#
# Strategy:
#   1. pkg install  — for packages Termux ships as native ARM64 binaries
#   2. pip install --only-binary :all:  — pre-built wheels only, no compilation
#   3. pip install  — fallback with compilation allowed (last resort)

set -e
cd "$(dirname "$0")/.."   # project root

pkg_try() {
    # Try installing a pkg package; silently skip if not found
    pkg install -y "$1" 2>&1 | grep -v "^$" | grep -v "^Reading\|^Building\|^Calculating" || true
}

echo "[JARVIS] updating Termux package lists..."
pkg update -y 2>/dev/null || true

# ── 1. pkg for known-available native packages ────────────────────────────────
echo ""
echo "[JARVIS] step 1: pkg install for native ARM64 packages..."
pkg install -y python-numpy 2>/dev/null && echo "  ✓ numpy (pkg)" || echo "  - numpy (will try pip)"
pkg install -y python-scipy 2>/dev/null && echo "  ✓ scipy (pkg)" || echo "  - scipy (will try pip)"

# ── 2. pip --only-binary for packages without pkg entries ─────────────────────
echo ""
echo "[JARVIS] step 2: pip (binary wheels only, no compilation)..."
pip install --prefer-binary --only-binary=pandas,scikit-learn,hmmlearn \
    "pandas>=2.2.3" \
    "scikit-learn>=1.5.0" \
    "hmmlearn>=0.3.2" \
    2>/dev/null \
    && echo "  ✓ pandas, scikit-learn, hmmlearn (pip wheels)" \
    || {
        echo "  - binary-only install failed, trying with compilation allowed..."
        pip install "pandas>=2.2.3" "scikit-learn>=1.5.0" "hmmlearn>=0.3.2"
    }

# ── 3. Pure-Python packages (no compilation needed) ──────────────────────────
echo ""
echo "[JARVIS] step 3: pure-Python packages via pip..."
pip install \
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

echo ""
echo "[JARVIS] ✓ all dependencies installed"
echo "[JARVIS]   run: bash scripts/start_termux.sh"
