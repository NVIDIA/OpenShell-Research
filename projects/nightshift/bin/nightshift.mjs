#!/usr/bin/env node
// The `nightshift` command. Runs the TypeScript CLI directly through tsx, so a
// checkout linked with `npm link` or installed from `npm pack` needs no build step.
import { register } from 'tsx/esm/api'

register()
await import('../src/nightshift.ts')
