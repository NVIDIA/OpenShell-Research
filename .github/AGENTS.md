# Repository CI

- Keep dependency-license checks deterministic and independent of agent reviews.
  Run them without inference credentials or write permissions.
- The approved license list is policy, not a convenience setting. Do not broaden
  it or add package clarifications merely to make a failing check pass.
- Prefer Python for scripting. Keep lockfile validation in the native package
  manager rather than implementing a dependency resolver.
- Run the license checker's pinned unit tests as documented in
  `docs/development/dependency-licenses.md` when changing its workflow or policy.
