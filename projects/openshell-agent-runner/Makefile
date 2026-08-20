# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

.DEFAULT_GOAL := help

PUBLISH_FLAGS :=
ifdef DRY_RUN
PUBLISH_FLAGS += --dry-run
endif

.PHONY: help publish

help: ## Show available targets and configurable variables.
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf "\nVariables:\n"
	@printf "  VERSION=X.Y.Z  Required package version for publish\n"
	@printf "  DRY_RUN=1      Validate a release without tagging or uploading\n"

publish: ## Validate or publish a release; requires VERSION.
ifndef VERSION
	$(error VERSION is required, e.g. make publish VERSION=0.1.0 DRY_RUN=1)
endif
	./scripts/publish.sh $(VERSION) $(PUBLISH_FLAGS)
