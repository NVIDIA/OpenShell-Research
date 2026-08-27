# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

.DEFAULT_GOAL := help

UV ?= uv
UV_RUN := $(UV) run --frozen
PYTEST_ARGS ?=

PUBLISH_FLAGS :=
ifdef DRY_RUN
PUBLISH_FLAGS += --dry-run
endif
ifdef ALLOW_NON_MAIN
PUBLISH_FLAGS += --allow-non-main
endif
ifdef RETRY_ARTIFACT
PUBLISH_FLAGS += --retry-artifact $(RETRY_ARTIFACT)
endif

.PHONY: help sync test format-check lint typecheck check build clean publish

help: ## Show available targets and configurable variables.
	@printf "\033[1;36mOpenShell Agent Runner\033[0m\n"
	@printf "\033[2mUsage: make <target> [VARIABLE=value]\033[0m\n"
	@printf "\n\033[1;33m🛠  Development\033[0m\n"
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / && $$1 != "publish" {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf "\n\033[1;33m📦 Release\033[0m\n"
	@awk 'BEGIN {FS = ":.*## "}; $$1 == "publish" {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf "\n\033[1;33m⚙  Configuration\033[0m\n"
	@printf "  \033[32m%-20s\033[0m %s\n" "PYTEST_ARGS=..." "Extra pytest paths or flags"
	@printf "  \033[32m%-20s\033[0m %s\n" "VERSION=X.Y.Z" "Required package version for publish"
	@printf "  \033[32m%-20s\033[0m %s\n" "DRY_RUN=1" "Validate a release without tagging or uploading"
	@printf "  \033[32m%-20s\033[0m %s\n" "ALLOW_NON_MAIN=1" "Permit release validation or publishing off main"
	@printf "  \033[32m%-20s\033[0m %s\n" "RETRY_ARTIFACT=..." "Retry a missing wheel, sdist, or both after a failed upload"

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
	bash -n src/openshell_agent_runner/harnesses/pi/runtime/image/exec.sh

build: ## Build the source distribution and wheel.
	$(UV) build --clear --no-sources

clean: ## Remove build, test, lint, and Python cache artifacts.
	rm -rf build dist .pytest_cache .ruff_cache
	find src tests -type d -name __pycache__ -prune -exec rm -rf {} +

publish: ## Validate or publish a release; requires VERSION.
ifndef VERSION
	$(error VERSION is required, e.g. make publish VERSION=0.1.0 DRY_RUN=1)
endif
	./scripts/publish.sh $(VERSION) $(PUBLISH_FLAGS)
