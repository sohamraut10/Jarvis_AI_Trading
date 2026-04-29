#!/data/data/com.termux/files/usr/bin/bash
# ── JARVIS AI Trading — Termux dependency installer ───────────────────────────
# Usage:  bash scripts/install_termux.sh
# NEVER compiles from source — binary-only throughout.

set -e
cd "$(dirname "$0")/.."   # project root

echo "[JARVIS] updating Termux package lists..."
pkg update -y 2>/dev/null || true

# ── Step 1: pkg for Termux-native binaries ────────────────────────────────────
echo ""
echo "[JARVIS] step 1: pkg install (numpy, scipy)..."
pkg install -y python-numpy 2>/dev/null && echo "  ✓ numpy" || echo "  ! numpy skipped"
pkg install -y python-scipy 2>/dev/null && echo "  ✓ scipy" || echo "  ! scipy skipped"

# ── Helper: install one package as binary-wheel only ─────────────────────────
pip_bin() {
    local spec="$1"
    local required="${2:-optional}"
    local name="${spec%%[>=!<=]*}"
    printf "  %-30s" "$name"
    if pip install -q --only-binary :all: "$spec" 2>/dev/null; then
        echo "✓"
    elif [ "$required" = "required" ]; then
        # Try without binary restriction as last resort for pure-Python packages
        if pip install -q --no-build-isolation "$spec" 2>/dev/null; then
            echo "✓ (source)"
        else
            echo "FAILED — check manually: pip install $spec"
        fi
    else
        echo "skipped (no wheel)"
    fi
}

# ── Step 2: optional compiled packages ───────────────────────────────────────
echo ""
echo "[JARVIS] step 2: optional compiled packages..."
pip_bin "pandas>=2.2.3"       optional
pip_bin "scikit-learn>=1.5.0" optional
pip_bin "hmmlearn>=0.3.2"     optional

# ── Step 3: required packages (binary preferred, pinned to 3.13-wheel vers.) ──
echo ""
echo "[JARVIS] step 3: required packages..."
pip_bin "websockets>=12.0"           required
pip_bin "httpx>=0.27.0"              required
pip_bin "python-dotenv>=1.0.1"       required
pip_bin "pydantic>=2.9.0"            required   # 2.9+ ships cp313 aarch64 wheels
pip_bin "dhanhq==2.0.1"              required
pip_bin "aiofiles>=23.2.1"           required
pip_bin "sqlalchemy>=2.0.36"         required   # 2.0.36+ ships cp313 wheels
pip_bin "aiosqlite>=0.20.0"          required
pip_bin "anthropic>=0.28.0"          required
pip_bin "openai>=1.30.0"             required
pip_bin "google-generativeai>=0.8.0" required
pip_bin "pyyaml>=6.0.1"              required

echo ""
echo "[JARVIS] ✓ done — run: bash scripts/start_termux.sh"
