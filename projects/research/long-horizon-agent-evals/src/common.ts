import { appendFile, mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { OpenShellClient, type ConnectOptions } from '@nvidia/openshell-sdk'

export function required(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error(`missing required environment variable: ${name}`)
  return value
}

function parseEnvFile(text: string): Record<string, string> {
  const values: Record<string, string> = {}
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/)
    if (!match) continue
    const name = match[1]
    let value = match[2]?.trim() ?? ''
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1)
    }
    if (name) values[name] = value
  }
  return values
}

export async function loadEnv(file = path.resolve('.env')): Promise<void> {
  const text = await readFile(file, 'utf8').catch((error: NodeJS.ErrnoException) => {
    if (error.code === 'ENOENT') return ''
    throw error
  })
  for (const [name, value] of Object.entries(parseEnvFile(text))) {
    if (process.env[name] === undefined) process.env[name] = value
  }
}

export function integer(name: string, fallback: number): number {
  const raw = process.env[name]
  if (!raw) return fallback
  const value = Number.parseInt(raw, 10)
  if (!Number.isFinite(value) || value <= 0) throw new Error(`${name} must be a positive integer`)
  return value
}

export function json(value: unknown): string {
  return JSON.stringify(value, (_key, item) => (typeof item === 'bigint' ? item.toString() : item), 2)
}

export function redactUntrusted(value: unknown): unknown {
  if (typeof value === 'string') {
    return value
      .replace(/github_pat_[A-Za-z0-9_]+/g, '[redacted-github-token]')
      .replace(/gh[opurs]_[A-Za-z0-9_]+/g, '[redacted-github-token]')
      .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*\b/g, '[redacted-jwt]')
      .replace(/([?&](?:access_token|auth|token)=)[^&\s"'\\]+/gi, '$1[redacted-query-token]')
  }
  if (Array.isArray(value)) return value.map(redactUntrusted)
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, redactUntrusted(item)]))
  }
  return value
}

export function redactKnown(text: string, secrets: string[]): string {
  let result = text
  for (const secret of secrets) {
    if (secret) result = result.replaceAll(secret, '[redacted]')
  }
  return redactUntrusted(result) as string
}

export function status(event: string, fields: Record<string, unknown> = {}): void {
  process.stdout.write(`${JSON.stringify({
    timestamp: new Date().toISOString(),
    runId: process.env.LAB_RUN_ID ?? null,
    event,
    ...fields,
  })}\n`)
}

export async function writeJson(file: string, value: unknown): Promise<void> {
  await mkdir(path.dirname(file), { recursive: true })
  await writeFile(file, `${json(value)}\n`)
}

export async function appendJsonl(file: string, value: unknown): Promise<void> {
  await mkdir(path.dirname(file), { recursive: true })
  const record = { timestamp: new Date().toISOString(), ...((value as object) ?? {}) }
  await appendFile(file, `${JSON.stringify(record, (_key, item) => (typeof item === 'bigint' ? item.toString() : item))}\n`)
}

async function optionalFile(name: string): Promise<Buffer | undefined> {
  const file = process.env[name]
  return file ? readFile(file) : undefined
}

export async function connect(): Promise<OpenShellClient> {
  const options: ConnectOptions = {
    gateway: process.env.LAB_OPENSHELL_GATEWAY ?? process.env.OPENSHELL_GATEWAY_ENDPOINT ?? 'http://127.0.0.1:8080',
    caCert: await optionalFile('OPENSHELL_CA_CERT'),
    clientCert: await optionalFile('OPENSHELL_CLIENT_CERT'),
    clientKey: await optionalFile('OPENSHELL_CLIENT_KEY'),
    oidcToken: process.env.OPENSHELL_TOKEN || undefined,
    insecureSkipVerify: process.env.OPENSHELL_INSECURE === '1',
  }
  const client = await OpenShellClient.connect(options)
  await client.health()
  return client
}

export async function delay(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms))
}
