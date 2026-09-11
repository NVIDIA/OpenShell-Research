// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package storagehealthextension

import (
	"bytes"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"testing"
)

var (
	storageHealthDeclaration = regexp.MustCompile(`(?m)^  storagehealth:\s*$`)
	storageHealthListEntry   = regexp.MustCompile(`(?m)^\s+- storagehealth\s*$`)
	storageHealthInlineEntry = regexp.MustCompile(`(?m)^  extensions:\s*\[[^\]]*storagehealth[^\]]*\]\s*$`)
)

func TestEveryDurableConfigurationEnablesStorageHealth(t *testing.T) {
	t.Parallel()
	repositoryRoot := filepath.Clean(filepath.Join("..", ".."))
	checked := 0
	err := filepath.WalkDir(repositoryRoot, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			switch entry.Name() {
			case ".git", ".cache", "_build", "dist", "runtime":
				return filepath.SkipDir
			}
			return nil
		}
		if extension := filepath.Ext(path); extension != ".yaml" && extension != ".yml" {
			return nil
		}
		contents, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if !bytes.Contains(contents, []byte("file_storage/")) {
			return nil
		}
		checked++
		if !storageHealthDeclaration.Match(contents) {
			t.Errorf("%s configures durable storage without a storagehealth extension", path)
		}
		if !storageHealthListEntry.Match(contents) && !storageHealthInlineEntry.Match(contents) {
			t.Errorf("%s declares storagehealth without enabling it in service.extensions", path)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if checked < 10 {
		t.Fatalf("checked %d durable configurations, want at least 10", checked)
	}
}
