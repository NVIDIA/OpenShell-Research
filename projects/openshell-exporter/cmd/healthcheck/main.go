// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

// Command healthcheck probes Collector health or verifies writable persistence paths without a shell.
package main

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"time"
)

func main() {
	if len(os.Args) > 1 {
		if os.Args[1] != "--writable" || len(os.Args) < 3 {
			fmt.Fprintln(os.Stderr, "usage: healthcheck [--writable DIRECTORY ...]")
			os.Exit(2)
		}
		for _, directory := range os.Args[2:] {
			if err := checkWritable(directory); err != nil {
				fmt.Fprintln(os.Stderr, err)
				os.Exit(1)
			}
		}
		return
	}
	if err := probeHealth(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func probeHealth() error {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://127.0.0.1:13133/", nil)
	if err != nil {
		return err
	}
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer func() {
		_ = response.Body.Close()
	}()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("health endpoint returned %s", response.Status)
	}
	return nil
}

func checkWritable(directory string) error {
	file, err := os.CreateTemp(directory, ".openshell-write-check-")
	if err != nil {
		return fmt.Errorf("persistence directory %s is not writable: %w", directory, err)
	}
	name := file.Name()
	if err := file.Close(); err != nil {
		_ = os.Remove(name)
		return fmt.Errorf("close persistence probe in %s: %w", directory, err)
	}
	if err := os.Remove(name); err != nil {
		return fmt.Errorf("remove persistence probe in %s: %w", directory, err)
	}
	return nil
}
