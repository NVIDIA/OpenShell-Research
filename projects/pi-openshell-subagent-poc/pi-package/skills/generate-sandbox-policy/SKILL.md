---
name: generate-sandbox-policy
description: Author the complete, least-privilege OpenShell YAML policy required for an OpenShell child sandbox before delegating work to openshell-worker.
---

# Generate an OpenShell child policy

Use this skill whenever the parent Pi agent creates an `openshell-worker`
subagent. Translate the child's task into one complete OpenShell policy and
return raw YAML suitable for an `<openshell-policy>` block.

Start with this Pi worker baseline. Do not remove baseline paths needed to boot
and run Pi:

```yaml
version: 1
filesystem_policy:
  include_workdir: true
  read_only:
    - /usr
    - /lib
    - /lib64
    - /proc
    - /etc
    - /app
    - /opt
    - /var/log
    - /dev/urandom
  read_write:
    - /sandbox
    - /tmp
    - /dev/null
    - /home/sandbox
landlock:
  compatibility: best_effort
process:
  run_as_user: sandbox
  run_as_group: sandbox
network_policies: {}
```

Add only the network access required by the assigned task:

- Prefer exact hosts, ports, HTTP methods, and paths.
- Bind each network policy to only the binaries that need it.
- Use `protocol: rest` with explicit `rules` when method or path restrictions
  matter. Do not combine `access` and `rules` on one endpoint.
- Omit `tls`; OpenShell detects TLS automatically.
- Do not add gateway access, OpenShell credentials, Git write operations, or
  unrelated endpoints.
- Inference access is configured separately by the Tool Service and does not
  belong in this policy.

For read-only clone or fetch of one public GitHub repository, replace the empty
`network_policies` map with the block below. Substitute the exact `OWNER/REPO`;
do not use wildcards:

```yaml
network_policies:
  github_repository:
    name: github-repository-read-only
    endpoints:
      - host: github.com
        port: 443
        protocol: rest
        enforcement: enforce
        rules:
          - allow:
              method: GET
              path: /OWNER/REPO.git/info/refs
              query:
                service: git-upload-pack
          - allow:
              method: POST
              path: /OWNER/REPO.git/git-upload-pack
          - allow:
              method: GET
              path: /OWNER/REPO/info/refs
              query:
                service: git-upload-pack
          - allow:
              method: POST
              path: /OWNER/REPO/git-upload-pack
    binaries:
      - path: /usr/bin/git
      - path: /usr/local/bin/git
```

Before returning the policy, check that it contains `version: 1`, the complete
Pi worker baseline, and only task-required network access. Present the policy;
do not write it to a file and do not invoke OpenShell CLI commands.

This POC does not yet prove that the child policy is a subset of the parent
policy. Do not claim that attenuation has been verified.
