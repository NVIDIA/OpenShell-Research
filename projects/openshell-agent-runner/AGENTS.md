# OpenShell Agent Runner development instructions

- Keep the package focused on launching explicitly configured agents. Do not
  add Git, repository inspection, provider management, or inference mutation.
- Preserve native OpenShell option names and transfer semantics.
- Keep profiles strict and declarative; reject unknown keys and trusted-resource
  paths that escape their profile directory.
- Never put credentials in configuration, environment forwarding, logs, or
  fixtures.
- Treat caller uploads as disposable writable agent workspace. Only the task's
  declared output may be downloaded. Image-baked `/opt/oar` assets are
  read-only; native per-run resources under `/sandbox/oar-runtime` are writable
  because OpenShell cannot upload into a read-only path. Host Pydantic validation
  is the structural output boundary; it does not attest agent-produced claims.
- Use `apply_patch` for edits and `uv` for dependencies, builds, and execution.
- Before handing off, run `uv sync --locked`, Ruff, ty, pytest, and `uv build`.
