// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package storagehealthextension

import (
	"errors"
	"fmt"
	"strings"
	"time"
)

const (
	defaultInterval   = 30 * time.Second
	defaultMaxEntries = 1024
	minimumInterval   = time.Second
)

var allowedStorageNames = map[string]struct{}{
	"checkpoints":       {},
	"cloudevents_queue": {},
	"otlp_grpc_queue":   {},
	"otlp_http_queue":   {},
	"recovery":          {},
}

type Config struct {
	Interval   time.Duration     `mapstructure:"interval"`
	MaxEntries int               `mapstructure:"max_entries"`
	Paths      map[string]string `mapstructure:"paths"`
}

func (config *Config) Validate() error {
	if config.Interval < minimumInterval {
		return fmt.Errorf("interval must be at least %s", minimumInterval)
	}
	if config.MaxEntries < 1 || config.MaxEntries > 10_000 {
		return errors.New("max_entries must be between 1 and 10000")
	}
	if len(config.Paths) == 0 {
		return errors.New("at least one durable path is required")
	}
	for name, path := range config.Paths {
		if _, ok := allowedStorageNames[name]; !ok {
			return fmt.Errorf("unsupported durable path name %q", name)
		}
		if strings.TrimSpace(path) == "" {
			return fmt.Errorf("durable path %q is empty", name)
		}
	}
	return nil
}
