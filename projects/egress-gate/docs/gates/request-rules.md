---
title: Request-rules gate
description: Deterministically allow or deny requests from structured facts.
agent_markdown: true
---

# Request-rules gate

`request-rules` matches the current OpenShell request using structured facts;
it never inspects the body. A rule's `match` fields are an AND: every field
that is present must match. Values in a field are OR, except
`headers_present`, whose names are an ALL-of condition.

```yaml
gate: request-rules
rules:
  - name: deny-destructive-github
    match:
      hosts: [api.github.com]
      methods: [DELETE]
    decision: deny
    reason_code: destructive_method_denied
  - name: allow-known-read
    match:
      schemes: [https]
      hosts: [api.github.com]
      ports: [443]
      methods: [GET, HEAD]
      path:
        type: glob
        value: /repos/acme/*
      headers_present: [x-request-id]
      process_binaries: [/usr/local/bin/agent]
      ancestor_binaries: [/bin/sh]
    decision: allow
```

The configuration contains one through 256 uniquely named rules. Each tuple
match field contains at most 64 unique values after normalization. A match must
contain at least one non-empty condition. A rule is either `allow` or `deny`;
only a deny rule has a `reason_code`, and it is required for every deny rule.
There is no `no_match` setting. If no rule matches, the gate returns
`proceed`, leaving the pipeline default in control.

## Matching and normalization

- `schemes` are compared case-insensitively by lowercasing ASCII scheme names.
- `hosts` are exact host fields, not complete authorities. Userinfo, embedded
  ports, and bracketed IPv6 forms are rejected. IP literals are compared in
  their canonical form. ASCII DNS labels, including A-labels, are lowercased;
  Unicode host normalization is not performed. A terminal DNS root dot is
  canonicalized away, and ports are never inferred from a scheme.
- `ports` are exact integer comparisons in the range 1 through 65535.
- `methods` are case-sensitive ASCII HTTP tokens. Process binary paths,
  ancestor binary paths, and paths are case-sensitive and are not trimmed or
  normalized. Process matching does not use a basename.
- Header names in `headers_present` and the visible request headers are
  lowercased for case-insensitive presence checks. Header values are ignored;
  repeated fields do not change the result.
- An exact path uses equality and a prefix path uses the raw string prefix.
  A glob treats only `*` as special; it matches any number of characters,
  including `/`, and has a maximum of 64 wildcard characters. Raw `?` and `#`
  delimiters are rejected because query and fragment data are separate from
  the request path. The matcher is bounded and does not use regular
  expressions or `fnmatch`.
- Path matching does not percent-decode, Unicode-normalize, collapse dot
  segments, collapse slashes, or incorporate the separate query field.

Within one gate, matching denies are checked first and the first matching deny
in configuration order wins. If no deny matches, the first matching allow in
configuration order wins. A winning allow is terminal and prevents later
pipeline gates from running.

Each winner emits exactly one five-field finding:

```text
type=request_rule_match
label=<configured rule name>
count=1
severity=allow|deny
```

The finding contains no request-derived host, path, query, header value,
process, or body content. A winning deny uses the rule's configured reason
code; a winning allow has no reason code.
