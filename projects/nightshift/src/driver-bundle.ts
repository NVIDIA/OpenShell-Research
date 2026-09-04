/**
 * Bundle the in-sandbox driver (driver/driver.ts and everything it imports)
 * into a single ES module. The host streams the result into the sandbox and
 * runs it with `node`, so the sandbox needs no npm install and no source tree.
 */
import { build } from 'esbuild'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))

export async function bundleDriver(): Promise<Buffer> {
  const result = await build({
    entryPoints: [path.join(root, 'driver', 'driver.ts')],
    bundle: true,
    platform: 'node',
    format: 'esm',
    target: 'node22',
    write: false,
    logLevel: 'silent',
  })
  const output = result.outputFiles[0]
  if (!output) throw new Error('driver bundle produced no output')
  return Buffer.from(output.text)
}
