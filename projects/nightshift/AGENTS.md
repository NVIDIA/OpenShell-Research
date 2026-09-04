# Nightshift

Read `README.md` and `docs/index.md` before changing this project. Run commands
from this directory with Node.js 20.3 or newer.

```shell
npm run check
```

Then run the zero-credential smoke experiment against the local gateway before
handing off any change to the harness, driver, or a runtime:

```shell
npm run nightshift -- run hello-canary
```

## Design rules

- Four roles, nothing else: experiment (task, opening policy, credentials,
  scorer), agent runtime, reviewer, and the harness core that runs them.
- `src/openshell.ts` is the only module that imports the OpenShell SDK.
- Reviewers and runtimes are listed in explicit maps (`src/registry.ts`,
  `driver/runtimes/index.ts`). Experiments are folders the CLI is pointed at.
- Do not add an interface, option, or event type for a hypothetical third
  implementation. Two real implementations in the same change, or nothing.
- An experiment is one folder: `experiment.json`, `program.md`, `policy.json`,
  optionally `reviewer.md`, `workdir/`, `image/`, and a `score.ts` of host-side
  hooks only when a command inside the sandbox cannot produce the score. Keep
  instance-specific facts in `Instance.facts` and every literal secret in
  `Instance.secrets` so evidence is redacted.
- The score is produced on the trusted side and the ledger is written by the
  harness. Never take a score from the agent's prose.
- The driver (`driver/`) runs inside the sandbox as one bundled file and never
  reads `.env`; all of its configuration arrives in `DriverConfig`.
- Prefer deleting to abstracting. Shell scripts stay under 30 lines; logic
  lives in TypeScript. Judge every file against autoresearch's three-file bar.

## Safety

Never commit `.env`, credentials, or files under `runs/`. The
`github-policy-review` experiment performs real GitHub mutations with the token
you give it and hands that token to an adversarial agent: use a disposable
repository and a token scoped only to it.
