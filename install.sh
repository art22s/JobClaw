#!/bin/bash
# install.sh — One-command setup for job-search
set -e

echo "🦞 Job Search — Installing..."

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3.10+ required. Install it first."
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  Python: $PY_VER ✓"

# Install Playwright (needed for Workday rendering)
if python3 -c "from playwright.async_api import async_playwright" 2>/dev/null; then
    echo "  Playwright: installed ✓"
else
    echo "  Installing Playwright..."
    pip install playwright
    playwright install chromium
    echo "  Playwright: installed ✓"
fi

# Make CLI executable
chmod +x "$SCRIPT_DIR/job_search.py"
ln -sf "$SCRIPT_DIR/job_search.py" /usr/local/bin/job-search 2>/dev/null || \
    echo "  (Could not symlink to /usr/local/bin — add $SCRIPT_DIR to PATH or use python3 job_search.py)"

# Create config dir
mkdir -p "$HOME/.config/job-search-3"

# Copy example profile if none exist
if [ ! -f "$SCRIPT_DIR/profiles/example.md" ]; then
    echo "  ⚠ No profiles found. Create one in profiles/ before running."
fi

echo ""
echo "✅ Installed! Usage:"
echo "  python3 $SCRIPT_DIR/job_search.py run --profile profiles/example.md"
echo ""
echo "Optional: Set up Google Sheet sync with gog CLI:"
echo "  gog auth login"
echo "  gog auth tokens export --out ~/.config/job-search-3/gog_token.json"
