// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

//go:build !windows

package main

import "go.opentelemetry.io/collector/otelcol"

func run(params otelcol.CollectorSettings) error {
	return runInteractive(params)
}
