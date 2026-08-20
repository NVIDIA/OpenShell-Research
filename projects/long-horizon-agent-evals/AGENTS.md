# Long-Horizon Agent Evals

Read `README.md` before changing this project. Keep the GitHub experiment wired
directly into `src/campaign.ts`; do not add a scenario framework until a second
real experiment demonstrates shared code.

Use Node.js 20.3 or newer and npm. Run commands from this directory.

```shell
npm run check
```

Never commit `.env`, credentials, or files under `runs/`. Treat the GitHub
preflight and campaigns as real mutations: use a disposable repository and a
repository-scoped token.
