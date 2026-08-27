import { stat } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { summarizeUsage } from './usage.js'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))

async function main(): Promise<void> {
  const arguments_ = process.argv.slice(2)
  if (arguments_.length === 0) throw new Error('usage: npm run costs -- <run-directory> [...]')
  let total = 0
  let lowerBound = false
  process.stdout.write('run\tchallenger\treviewer\ttotal_usd\tcoverage\tinput\tcached\toutput\n')
  for (const argument of arguments_) {
    const direct = path.resolve(argument)
    const runDir = await stat(direct).then(() => direct).catch(() => path.join(root, 'runs', argument))
    const usage = await summarizeUsage(runDir)
    total += usage.estimatedCostUsd
    lowerBound ||= usage.coverage.estimateIsLowerBound
    process.stdout.write([
      path.basename(runDir),
      usage.byRole.challenger.estimatedCostUsd.toFixed(4),
      usage.byRole.reviewer.estimatedCostUsd.toFixed(4),
      usage.estimatedCostUsd.toFixed(4),
      usage.coverage.estimateIsLowerBound ? 'lower_bound' : 'complete',
      usage.tokens.input,
      usage.tokens.cachedInput,
      usage.tokens.output,
    ].join('\t') + '\n')
  }
  process.stdout.write(`TOTAL\t\t\t${total.toFixed(4)}\t${lowerBound ? 'lower_bound' : 'complete'}\n`)
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`)
  process.exitCode = 1
})
