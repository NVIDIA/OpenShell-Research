import { parseArgs } from 'node:util'

type Environment = Record<string, string | undefined>

function positiveInteger(name: string, raw: string): number {
  if (!/^[1-9]\d*$/.test(raw)) throw new Error(`${name} must be a positive integer`)
  const value = Number(raw)
  if (!Number.isSafeInteger(value)) throw new Error(`${name} must be a positive integer`)
  return value
}

function value(name: string, cli: string | undefined, env: string | undefined, fallback: number): number {
  return positiveInteger(name, cli ?? env ?? String(fallback))
}

export function campaignRuntimeOptions(args: string[], env: Environment = process.env): { minutes: number } {
  const { values } = parseArgs({
    args,
    strict: true,
    allowPositionals: false,
    options: { minutes: { type: 'string' } },
  })
  return { minutes: value('--minutes', values.minutes, env.LAB_DURATION_MINUTES, 30) }
}

export function scaleRuntimeOptions(
  args: string[],
  env: Environment = process.env,
): { minutes: number; runs: number; concurrency: number } {
  const { values } = parseArgs({
    args,
    strict: true,
    allowPositionals: false,
    options: {
      minutes: { type: 'string' },
      runs: { type: 'string' },
      concurrency: { type: 'string' },
    },
  })
  const runs = value('--runs', values.runs, env.LAB_RUNS, 50)
  return {
    minutes: value('--minutes', values.minutes, env.LAB_DURATION_MINUTES, 30),
    runs,
    concurrency: value('--concurrency', values.concurrency, env.LAB_CONCURRENCY, 2),
  }
}
