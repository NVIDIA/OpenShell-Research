# Long-Horizon Agent Evals

Read `README.md` and `docs/index.md` before changing this project. Run commands
from this directory with Node.js 20.3 or newer.

```shell
npm run check
```

Then run the zero-credential smoke scenario against the local gateway before
handing off any change to the harness, driver, or a runtime:

```shell
npm run lab -- run hello-canary
```

## Design rules

- Four roles, nothing else: scenario (task, initial policy, credentials,
  oracle), agent runtime, reviewer, and the harness core that runs them.
- `src/openshell.ts` is the only module that imports the OpenShell SDK.
- Scenarios, reviewers, and runtimes are listed in explicit maps
  (`src/registry.ts`, `driver/runtimes/index.ts`). No filesystem discovery.
- Do not add an interface, option, or event type for a hypothetical third
  implementation. Two real implementations in the same change, or nothing.
- A scenario is one folder: `scenario.json`, `task.md`, and a `scenario.ts`
  of about 100 lines. Keep instance-specific facts in `Instance.facts` and
  every literal secret in `Instance.secrets` so evidence is redacted.
- The driver (`driver/`) runs inside the sandbox as one bundled file and never
  reads `.env`; all of its configuration arrives in `DriverConfig`.
- Prefer deleting to abstracting. Shell scripts stay under 30 lines; logic
  lives in TypeScript.

## Safety

Never commit `.env`, credentials, or files under `runs/`. The
`github-policy-review` scenario performs real GitHub mutations with the token
you give it and hands that token to an adversarial agent: use a disposable
repository and a token scoped only to it.
