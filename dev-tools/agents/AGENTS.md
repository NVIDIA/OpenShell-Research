# Repository agent development instructions

- Keep profiles declarative and independent of GitHub Actions.
- Keep harness adapters independent of profile-specific behavior and application
  policy.
- Keep inference provider selection independent of the selected harness.
- Reject unknown manifest keys and paths that escape the owning profile.
- Never store credentials, populated environment files, or provider secrets in
  a profile.
- Disable automatic Pi resource and OpenShell provider discovery. Load only
  explicitly declared tools, skills, prompts, schemas, and providers.
- Keep `helpers.py` a standalone utility; do not turn this tool into a Python
  package without a concrete need for reusable Python APIs.
- Run the unittest, Ruff, compile, and shell syntax checks documented in
  `README.md` before handing off changes.
