# OpenShell Middleware Init contributor guidance

Read `README.md` and `pyproject.toml` before changing this project.

- Keep initialization non-destructive: never merge into or replace an existing
  destination.
- Keep generated projects version-matched. The proto, generated bindings or
  build configuration, dependency lock, and manifest must describe one
  OpenShell release.
- Template markers use `__UPPER_SNAKE_CASE__`. Add every new marker to
  `TemplateContext.replacements` and cover it with rendering tests.
- Generated Python bindings are generator-owned. Do not add formatting or type
  checks that rewrite them.
- Tests for the initializer must be hermetic. Inject protocol download and
  project preparation collaborators instead of accessing GitHub, uv, or Cargo.

Run the validation commands documented in `README.md` after changes.
