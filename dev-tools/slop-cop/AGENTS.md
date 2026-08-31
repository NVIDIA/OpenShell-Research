# Slop Cop development instructions

- Keep documentation, comments, names, tests, and output focused on current
  behavior and repository requirements. Do not include design history or
  comparisons with other tools.
- Preserve the three rule extension paths: declarative phrase rules,
  declarative bounded-regex rules, and Python rules exported explicitly from
  `slop_cop.rules.custom.CUSTOM_RULES`.
- Keep detection separate from scoring. Rules return bounded signals; only the
  scorer determines points and policy decisions.
- Preserve exact source offsets through Markdown projection. Add focused
  positive, counterexample, masking, and boundary tests with every change.
- Use `uv` for dependency management and execution. Run Ruff, mypy, and pytest
  before handing off changes.
- Do not add package discovery, entry-point loading, provider-specific model
  integrations, automatic rewriting, or persisted generated reports.

