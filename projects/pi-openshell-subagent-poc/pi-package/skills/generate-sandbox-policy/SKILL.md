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
- Do not add permissions unrelated to the assigned task.
- Do not impose blanket task-category restrictions. If the task requires Git
  writes, a gateway endpoint, or another capability, author the narrowest valid
  policy that permits that capability. The Tool Service reviewer decides
  whether the proposed child policy exceeds the live parent policy.
- Inference access is configured separately by the Tool Service and does not
  belong in this policy.

For read-only clone or fetch of one public GitHub repository, replace the empty
`network_policies` map with the block below. Substitute the exact `OWNER/REPO`;
do not use wildcards. This is an example for a read-only task, not a restriction
on other Git operations:

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

For Git push, add only the repository-scoped smart-HTTP operations required by
the task. Smart-HTTP push uses `GET /OWNER/REPO.git/info/refs` with
`service: git-receive-pack` and `POST /OWNER/REPO.git/git-receive-pack`.
Include the repository-scoped `git-upload-pack` operations too when the worker
must clone or fetch before pushing. GitHub Contents API writes instead use the
exact required `api.github.com` `PUT /repos/OWNER/REPO/contents/...` paths.
Avoid broad `access: read-write` when explicit REST rules can describe the
task. Network policy does not itself supply a GitHub credential;
credential/provider attachment is a separate Tool Service resource decision.
Do not invent credentials or embed secrets in the policy.

Before returning the policy, check that it contains `version: 1`, the complete
Pi worker baseline, and only task-required authority. Present the policy; do
not write it to a file and do not invoke OpenShell CLI commands.

Do not reject a requested capability merely because the parent policy may not
contain it. Author the least-privilege child policy required by the task. The
Tool Service retrieves the live parent policy and makes the allow-or-deny
attenuation decision before creating the child sandbox.

If that review returns `POLICY_ADVISOR_ACTION_REQUIRED`, use OpenShell Policy
Advisor only for the missing network `addRule` operations. Read
`/etc/openshell/skills/policy-advisor/SKILL.md`, submit the minimal proposal from
the parent sandbox, wait for human approval and `policy_reloaded: true`, then
launch a new worker request with the same child policy. Policy Advisor does not
cover filesystem, process, provider attachment, credential binding, or every
advanced network-policy field; report those as requiring a manual parent-policy
update.
