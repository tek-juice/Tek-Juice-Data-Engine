# ─────────────────────────────────────────────
# DATA ENGINE — Makefile
# ─────────────────────────────────────────────

.PHONY: help install migrate seed test lint format health logs backup deploy clean

# Default target
help:
	@echo ""
	@echo "  DATA ENGINE — Available Commands"
	@echo "  ─────────────────────────────────────────────"
	@echo "  make install     Install Python dependencies"
	@echo "  make up          Start all Docker services"
	@echo "  make down        Stop all Docker services"
	@echo "  make migrate     Run database migrations"
	@echo "  make seed        Seed initial data"
	@echo "  make test        Run full test suite"
	@echo "  make test-unit   Run unit tests only"
	@echo "  make test-int    Run integration tests only"
	@echo "  make lint        Run code linting"
	@echo "  make format      Auto-format code"
	@echo "  make health      Check all service healthchecks"
	@echo "  make logs        Tail all service logs"
	@echo "  make backup      Backup database and vectors"
	@echo "  make restore     Restore from latest backup"
	@echo "  make deploy      Deploy to production"
	@echo "  make clean       Remove compiled files and caches"
	@echo ""

# ── Setup ─────────────────────────────────────
install:
	pip install --upgrade pip
	pip install -r requirements.txt
	pre-commit install

# ── Docker ────────────────────────────────────
up:
	docker-compose up -d

down:
	docker-compose down

build:
	docker-compose build

restart:
	docker-compose restart

# ── Database ──────────────────────────────────
migrate:
	bash scripts/migrate.sh

seed:
	python -m database.seeds.seed_all

# ── Testing ───────────────────────────────────
test:
	pytest tests/ -v --cov=. --cov-report=term-missing --cov-report=html

test-unit:
	pytest tests/unit/ -v

test-int:
	pytest tests/integration/ -v

test-perf:
	pytest tests/performance/ -v

test-e2e:
	pytest tests/end_to_end/ -v

# ── Code Quality ──────────────────────────────
lint:
	ruff check .
	mypy . --ignore-missing-imports

format:
	black .
	ruff check . --fix

# ── Monitoring ────────────────────────────────
health:
	@echo "Checking service health..."
	@curl -sf http://localhost:8000/health || echo "FAIL: api_gateway"
	@curl -sf http://localhost:8001/health || echo "FAIL: ingestion_service"
	@curl -sf http://localhost:8003/health || echo "FAIL: embedding_service"
	@curl -sf http://localhost:8004/health || echo "FAIL: vector_vault"
	@curl -sf http://localhost:8005/health || echo "FAIL: telemetry_service"
	@curl -sf http://localhost:8011/health || echo "FAIL: dashboard_backend"
	@echo "Health check complete."

logs:
	docker-compose logs -f --tail=100

# ── Backup / Restore ──────────────────────────
backup:
	bash scripts/backup.sh

restore:
	bash scripts/restore.sh

# ── Deployment ────────────────────────────────
deploy:
	bash scripts/deploy.sh

# ── Cleanup ───────────────────────────────────
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	@echo "Clean complete."
