# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

.DEFAULT_GOAL := help

UV ?= uv
UV_RUN := $(UV) run --frozen
PYTEST_ARGS ?=

.PHONY: help sync test format-check lint typecheck check clean

help: ## Show available targets and configurable variables.
	@printf "\033[1;36mCedar Lexer\033[0m\n"
	@printf "\033[2mUsage: make <target> [VARIABLE=value]\033[0m\n"
	@printf "\n\033[1;33m🛠  Development\033[0m\n"
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf "\n\033[1;33m⚙  Configuration\033[0m\n"
	@printf "  \033[32m%-20s\033[0m %s\n" "PYTEST_ARGS=..." "Extra pytest paths or flags"

sync: ## Install locked runtime and development dependencies.
	$(UV) sync --locked

test: ## Run tests; pass paths or flags with PYTEST_ARGS.
	$(UV_RUN) pytest $(PYTEST_ARGS)

format-check: ## Check Python formatting.
	$(UV_RUN) ruff format --check .

lint: ## Run Ruff lint checks.
	$(UV_RUN) ruff check .

typecheck: ## Run ty type checks.
	$(UV_RUN) ty check

check: sync format-check lint typecheck test ## Run the full project checks.
	$(UV_RUN) python -m compileall -q src tests

clean: ## Remove test, lint, and Python cache artifacts.
	rm -rf .pytest_cache .ruff_cache
	find src tests -type d -name __pycache__ -prune -exec rm -rf {} +
