// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

// These projections are source facts, not decisions inferred from messages or
// numeric codes. Read the redacted original so renamed fields cannot bypass
// key-based redaction. Unknown and malformed values remain in original.
var ocsfContextFields = []struct {
	group, name string
	path        []string
	number      bool
}{
	{"actor", "process_name", []string{"actor", "process", "name"}, false},
	{"actor", "process_pid", []string{"actor", "process", "pid"}, true},
	{"actor", "process_uid", []string{"actor", "process", "uid"}, false},
	{"actor", "user_name", []string{"actor", "user", "name"}, false},
	{"actor", "user_uid", []string{"actor", "user", "uid"}, false},
	{"action", "activity_id", []string{"activity_id"}, true},
	{"action", "activity_name", []string{"activity_name"}, false},
	{"action", "api_operation", []string{"api", "operation"}, false},
	{"target", "domain", []string{"dst_endpoint", "domain"}, false},
	{"target", "ip", []string{"dst_endpoint", "ip"}, false},
	{"target", "port", []string{"dst_endpoint", "port"}, true},
	{"target", "file_name", []string{"file", "name"}, false},
	{"target", "file_path", []string{"file", "path"}, false},
	{"target", "file_uid", []string{"file", "uid"}, false},
	{"target", "api_service", []string{"api", "service", "name"}, false},
	{"outcome", "status_id", []string{"status_id"}, true},
	{"outcome", "status", []string{"status"}, false},
	{"outcome", "status_detail", []string{"status_detail"}, false},
	{"outcome", "action_id", []string{"action_id"}, true},
	{"outcome", "action", []string{"action"}, false},
	{"outcome", "disposition_id", []string{"disposition_id"}, true},
	{"outcome", "disposition", []string{"disposition"}, false},
	{"policy", "rule_name", []string{"firewall_rule", "name"}, false},
	{"policy", "rule_type", []string{"firewall_rule", "type"}, false},
	{"policy", "rule_uid", []string{"firewall_rule", "uid"}, false},
}

func addOCSFContext(envelope map[string]any, original any) {
	for _, field := range ocsfContextFields {
		value := original
		for _, segment := range field.path {
			object, ok := value.(map[string]any)
			if !ok {
				value = nil
				break
			}
			value = object[segment]
		}
		if field.number {
			integer, ok := exactInteger(value)
			if !ok || integer < 0 {
				continue
			}
			value = integer
		} else {
			text, ok := value.(string)
			if !ok || text == "" {
				continue
			}
		}
		group, ok := envelope[field.group].(map[string]any)
		if !ok {
			group = map[string]any{}
			envelope[field.group] = group
		}
		group[field.name] = value
	}
}
