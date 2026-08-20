# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

PUBLISH_FLAGS :=
ifdef DRY_RUN
PUBLISH_FLAGS += --dry-run
endif

.PHONY: publish

publish:
ifndef VERSION
	$(error VERSION is required, e.g. make publish VERSION=0.1.0 DRY_RUN=1)
endif
	./scripts/publish.sh $(VERSION) $(PUBLISH_FLAGS)
