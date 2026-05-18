.PHONY: install install-dev test test-unit test-integration test-e2e test-providers \
        lint format typecheck clean build-web dev-web help

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

install:  ## Install all Python packages in the workspace
	uv sync --all-packages

install-dev:  ## Install all packages including dev dependencies
	uv sync --all-packages --all-extras

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

test:  ## Run all tests
	uv run pytest tests/ -v

test-unit:  ## Run unit tests only
	uv run pytest tests/unit/ -v

test-integration:  ## Run integration tests only
	uv run pytest tests/integration/ -v

test-e2e:  ## Run end-to-end tests only
	uv run pytest tests/e2e/ -v

test-providers:  ## Run provider tests only (no API keys required)
	uv run pytest tests/providers/ -v

test-fast:  ## Run unit + provider tests (fast, no fixtures needed)
	uv run pytest tests/unit/ tests/providers/ -v -q

# ---------------------------------------------------------------------------
# Code Quality
# ---------------------------------------------------------------------------

lint:  ## Run ruff linter
	uv run ruff check packages/ tests/

format:  ## Run ruff formatter
	uv run ruff format packages/ tests/

format-check:  ## Check formatting without modifying files
	uv run ruff format --check packages/ tests/

typecheck:  ## Run mypy type checker
	uv run mypy packages/core/src packages/cli/src packages/server/src

check: lint format-check typecheck  ## Run all checks (no modifications)

fix: format lint  ## Format + lint with auto-fix

# ---------------------------------------------------------------------------
# Code Health (Phase 4)
# ---------------------------------------------------------------------------

health-check:  ## Run the code-health analyzer against this repo and fail on regressions
	uv run pytest tests/unit/health/ tests/unit/server/test_mcp.py -v
	uv run repowise health --format json > /tmp/repowise-health-report.json || true
	@echo "Health report → /tmp/repowise-health-report.json"

health-bench:  ## Run the 3,000-file health analyzer perf benchmark
	uv run pytest tests/integration/test_health_perf_benchmark.py -v -m slow

# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

dev-web:  ## Start Next.js dev server
	cd packages/web && npm run dev

build-web:  ## Build Next.js production output
	cd packages/web && npm run build

install-web:  ## Install Node dependencies for web package
	cd packages/web && npm install

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

clean:  ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name "*.pyo" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

build:  ## Build all Python distributions
	uv build --all-packages

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
