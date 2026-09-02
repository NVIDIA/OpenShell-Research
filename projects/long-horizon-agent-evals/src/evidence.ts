/**
 * Run evidence: where artifacts go and how they are written.
 *
 * Every run owns a directory under `runs/<run-id>/`. High-level progress is
 * newline-delimited JSON on stdout and in `<name>.jsonl`; structured artifacts
 * are pretty JSON. Known secrets are redacted from every file before the run
 * directory is considered shareable.
 */
import { appendFile, mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

/** Serialize with bigint support and stable indentation. */
export function json(value: unknown): string {
  return JSON.stringify(value, (_key, item) => (typeof item === 'bigint' ? item.toString() : item), 2)
}

export async function writeJson(file: string, value: unknown): Promise<void> {
  await mkdir(path.dirname(file), { recursive: true })
  await writeFile(file, `${json(value)}\n`)
}

export async function appendJsonl(file: string, value: Record<string, unknown>): Promise<void> {
  await mkdir(path.dirname(file), { recursive: true })
  const record = { timestamp: new Date().toISOString(), ...value }
  await appendFile(file, `${JSON.stringify(record, (_key, item) => (typeof item === 'bigint' ? item.toString() : item))}\n`)
}

/** Append already-serialized, newline-terminated text (e.g. a batch of JSON lines). */
export async function appendText(file: string, text: string): Promise<void> {
  await mkdir(path.dirname(file), { recursive: true })
  await appendFile(file, text)
}

export async function readJsonl(file: string): Promise<Array<Record<string, unknown>>> {
  const text = await readFile(file, 'utf8').catch(() => '')
  return text.split('\n').filter(Boolean).flatMap((line) => {
    try { return [JSON.parse(line) as Record<string, unknown>] } catch { return [] }
  })
}

/** Emit one progress event to stdout as a JSON line. */
export function status(event: string, fields: Record<string, unknown> = {}): void {
  process.stdout.write(`${JSON.stringify({ timestamp: new Date().toISOString(), event, ...fields })}\n`)
}

/** Replace exact known secrets, then well-known credential shapes. */
export function redact(text: string, secrets: string[]): string {
  let result = text
  for (const secret of secrets) if (secret) result = result.replaceAll(secret, '[redacted]')
  return result
    .replace(/github_pat_[A-Za-z0-9_]+/g, '[redacted-github-token]')
    .replace(/gh[opurs]_[A-Za-z0-9_]+/g, '[redacted-github-token]')
    .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*\b/g, '[redacted-jwt]')
    .replace(/([?&](?:access_token|auth|token)=)[^&\s"'\\]+/gi, '$1[redacted-query-token]')
}

/** Redact every file in the run directory in place; throw if a known secret survives. */
export async function redactRunDirectory(runDir: string, secrets: string[]): Promise<void> {
  for (const entry of await readdir(runDir, { withFileTypes: true })) {
    if (!entry.isFile()) continue
    const file = path.join(runDir, entry.name)
    const contents = await readFile(file, 'utf8')
    const redacted = redact(contents, secrets)
    if (redacted !== contents) await writeFile(file, redacted)
    for (const secret of secrets) {
      if (secret && redacted.includes(secret)) throw new Error(`secret redaction failed for ${entry.name}`)
    }
  }
}
