#!/usr/bin/env bash
# DATA ENGINE — Installation Script
# Sets up the full local development environment.
# Run from the project root: bash scripts/install.sh

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

echo -e "\n${BOLD}╔══════════════════════════════════════╗"
echo -e "║   DATA ENGINE — Installation         ║"
echo -e "╚══════════════════════════════════════╝${NC}\n"

# ── Prerequisites check ───────────────────────────────────────────────────────
info "Checking prerequisites..."

command -v python3 >/dev/null 2>&1 || error "Python 3 not found. Install Python 3.11+."
command -v pip    >/dev/null 2>&1 || error "pip not found."
command -v docker >/dev/null 2>&1 || warn  "Docker not found — Docker Compose stack won't be available."
command -v git    >/dev/null 2>&1 || error "git not found."

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
REQUIRED_MAJOR=3; REQUIRED_MINOR=11
ACTUAL_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
ACTUAL_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [[ "$ACTUAL_MAJOR" -lt "$REQUIRED_MAJOR" ]] || \
   [[ "$ACTUAL_MAJOR" -eq "$REQUIRED_MAJOR" && "$ACTUAL_MINOR" -lt "$REQUIRED_MINOR" ]]; then
    error "Python $REQUIRED_MAJOR.$REQUIRED_MINOR+ required. Found: $PYTHON_VERSION"
fi
success "Python $PYTHON_VERSION found."

# ── Virtual environment ───────────────────────────────────────────────────────
if [[ ! -d ".venv" ]]; then
    info "Creating virtual environment..."
    python3 -m venv .venv
    success "Virtual environment created at .venv"
else
    info "Virtual environment already exists — skipping creation."
fi

info "Activating virtual environment..."
# shellcheck disable=SC1091
source .venv/bin/activate

# ── Python dependencies ───────────────────────────────────────────────────────
info "Upgrading pip..."
pip install --quiet --upgrade pip

info "Installing Python dependencies from requirements.txt..."
pip install --quiet -r requirements.txt
success "Python dependencies installed."

# ── spaCy model ───────────────────────────────────────────────────────────────
info "Downloading spaCy English model..."
python3 -m spacy download en_core_web_sm --quiet || warn "spaCy model download failed — GEO NER will use rule-based fallback."

# ── Environment file ──────────────────────────────────────────────────────────
if [[ ! -f ".env" ]]; then
    if [[ -f ".env.example" ]]; then
        cp .env.example .env
        warn ".env created from .env.example — fill in your API keys before starting."
    else
        warn ".env not found and no .env.example exists. Create .env from the template in the docs."
    fi
else
    info ".env already exists — skipping."
fi

# ── Storage directories ───────────────────────────────────────────────────────
info "Creating storage directories..."
mkdir -p storage/uploads storage/processed storage/vectors storage/exports storage/logs
success "Storage directories ready."

# ── Pre-commit hooks ──────────────────────────────────────────────────────────
if command -v pre-commit >/dev/null 2>&1; then
    info "Installing pre-commit hooks..."
    pre-commit install
    success "Pre-commit hooks installed."
else
    warn "pre-commit not found — skipping hook installation."
fi

# ── Playwright browsers (for scraping) ───────────────────────────────────────
info "Installing Playwright browsers..."
python3 -m playwright install chromium --quiet 2>/dev/null || \
    warn "Playwright browser install failed — HTML scraping will use httpx fallback."

echo -e "\n${GREEN}${BOLD}Installation complete!${NC}"
echo -e "\nNext steps:"
echo -e "  1. Edit ${CYAN}.env${NC} with your API keys and database credentials"
echo -e "  2. Start services:  ${CYAN}docker-compose up -d${NC}"
echo -e "  3. Run migrations:  ${CYAN}make migrate${NC}"
echo -e "  4. Seed database:   ${CYAN}make seed${NC}"
echo -e "  5. Verify health:   ${CYAN}make health${NC}\n"
