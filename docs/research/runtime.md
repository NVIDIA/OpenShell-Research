# OpenShell Runtime Notes

Use this page for runtime assumptions that apply across OpenShell research
applications.

## Runtime Checklist

- Identify the process that owns the user-facing UI.
- Identify local services and ports the workflow depends on.
- Keep model/provider credentials outside the browser UI.
- Provide a fast local health check before the full app starts.
- Make logs and status endpoints easy to find.
- Document shutdown behavior for helper services.

## Open Questions

- Which runtime APIs should every example application demonstrate?
- Which checks belong in CI versus local preflight commands?
- Which deployment assumptions need a shared template?
