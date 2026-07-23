.DEFAULT_GOAL := help

UV ?= uv
UV_RUN := $(UV) run --frozen
PYTEST_ARGS ?=
PROFILE ?= /tmp/privacy-guard.prof

.PHONY: \
	help sync lock lock-check \
	test test-examples test-hardening \
	format format-check lint lint-fix typecheck fix \
	import-check build check check-py311 \
	benchmark benchmark-full benchmark-profile \
	clean

help: ## Show available targets and configurable variables.
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "  %-28s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf "\nVariables:\n"
	@printf "  PYTEST_ARGS=...  Extra pytest paths or flags\n"
	@printf "  PROFILE=...      Benchmark profile path (default: %s)\n" "$(PROFILE)"

sync: ## Install locked runtime and development dependencies.
	$(UV) sync --frozen

lock: ## Refresh uv.lock from pyproject.toml.
	$(UV) lock

lock-check: ## Verify uv.lock matches pyproject.toml.
	$(UV) lock --check

test: ## Run tests; pass paths or flags with PYTEST_ARGS.
	$(UV_RUN) pytest -q $(PYTEST_ARGS)

test-examples: ## Run example integration tests.
	$(UV_RUN) pytest -q tests/examples

test-hardening: ## Run security and typing-policy tests.
	$(UV_RUN) pytest -q tests/test_hardening.py tests/test_typing_policy.py

format: ## Format handwritten Python.
	$(UV_RUN) ruff format .

format-check: ## Check formatting without changing files.
	$(UV_RUN) ruff format --check .

lint: ## Run Ruff lint checks.
	$(UV_RUN) ruff check .

lint-fix: ## Apply safe Ruff fixes.
	$(UV_RUN) ruff check --fix .

typecheck: ## Run curated ty checks.
	$(UV_RUN) ty check

fix: ## Apply Ruff fixes, then format.
	$(UV_RUN) ruff check --fix .
	$(UV_RUN) ruff format .

import-check: ## Smoke-test the installed package import.
	$(UV_RUN) python -c "import privacy_guard"

build: ## Build the source distribution and wheel.
	$(UV) build

check: ## Run the authoritative full project checks.
	scripts/check.sh

check-py311: ## Run full checks on the minimum Python version.
	scripts/check.sh --python 3.11

benchmark: ## Run the quick processing benchmark.
	$(UV_RUN) python scripts/benchmark_privacy_guard.py

benchmark-full: ## Run the full processing benchmark suite.
	$(UV_RUN) python scripts/benchmark_privacy_guard.py --suite full

benchmark-profile: ## Run the quick benchmark and write PROFILE.
	$(UV_RUN) python scripts/benchmark_privacy_guard.py --profile "$(PROFILE)"

clean: ## Remove build, test, lint, and Python cache artifacts.
	rm -rf build dist .pytest_cache .ruff_cache
	find src tests examples scripts -type d -name __pycache__ -prune -exec rm -rf {} +
