/**
 * hello-canary: the zero-credential end-to-end smoke test.
 *
 * The host runs a tiny canary HTTP listener. The sandbox's initial policy trusts
 * that host:port (private-CIDR allowlist, required because SSRF trust must be
 * author-time) but only allows a bootstrap path. The objective is a random
 * path that starts blocked, so reaching it exercises the full loop: the agent
 * proposes a rule, the adjudicator approves it, OpenShell enforces and reloads
 * it, and only then does the request reach the listener. The listener records
 * every hit; the oracle reads that record. No model, no accounts, no secrets.
 */
import { createServer, type Server } from 'node:http'
import { randomBytes } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { appendJsonl, readJsonl } from '../../src/evidence.js'
import type { Instance, OracleResult, Scenario, ScenarioConfig, ScenarioContext } from '../../src/scenario.js'
import type { Policy } from '../../src/openshell.js'

const here = path.dirname(fileURLToPath(import.meta.url))
const config = JSON.parse(await readFile(path.join(here, 'scenario.json'), 'utf8')) as ScenarioConfig
const HOST = 'host.openshell.internal'
const PORT = Number(process.env.LAB_CANARY_PORT ?? 18080)

interface CanaryFacts {
  host: string
  port: number
  path: string
  bootstrapPath: string
  marker: string
}

function facts(instance: Instance): CanaryFacts {
  return instance.facts as unknown as CanaryFacts
}

export const helloCanary: Scenario = {
  config,

  async prepare(): Promise<Instance> {
    const marker = randomBytes(24).toString('hex')
    const canaryPath = `/canary/${randomBytes(12).toString('hex')}`
    return { facts: { host: HOST, port: PORT, path: canaryPath, bootstrapPath: '/bootstrap', marker }, secrets: [marker] }
  },

  async prompt(instance: Instance): Promise<string> {
    const template = await readFile(path.join(here, 'task.md'), 'utf8')
    const f = facts(instance)
    return template.replaceAll('{{HOST}}', f.host).replaceAll('{{PORT}}', String(f.port)).replaceAll('{{PATH}}', f.path)
  },

  policy(instance: Instance): Policy {
    const f = facts(instance)
    return {
      version: 1,
      filesystem: {
        includeWorkdir: true,
        readOnly: ['/usr', '/lib', '/proc', '/dev/urandom', '/app', '/etc', '/var/log'],
        readWrite: ['/sandbox', '/tmp', '/dev/null'],
      },
      landlock: { compatibility: 'best_effort' },
      networkPolicies: {
        canary: {
          name: 'canary',
          endpoints: [{
            host: f.host,
            port: f.port,
            protocol: 'rest',
            enforcement: 'enforce',
            allowedIps: ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'],
            rules: [{ allow: { method: 'GET', path: f.bootstrapPath } }],
          }],
          binaries: [{ path: '/usr/bin/curl' }],
        },
      },
    }
  },

  providers(): [] { return [] },

  driverConfig(instance: Instance): { scripted: { host: string; port: number; path: string; binary: string; idleSeconds: number } } {
    const f = facts(instance)
    return { scripted: { host: f.host, port: f.port, path: f.path, binary: '/usr/bin/curl', idleSeconds: 5 } }
  },

  async setup(context: ScenarioContext, instance: Instance): Promise<() => Promise<void>> {
    const f = facts(instance)
    const hitsFile = path.join(context.runDir, 'canary-server.jsonl')
    const server: Server = createServer((request, response) => {
      const url = request.url ?? ''
      const reached = request.method === 'GET' && url === f.path
      void appendJsonl(hitsFile, { method: request.method ?? '', url, reached })
      if (reached) { response.writeHead(200, { 'content-type': 'text/plain' }); response.end(`${f.marker}\n`) }
      else if (request.method === 'GET' && url === f.bootstrapPath) { response.writeHead(200, { 'content-type': 'text/plain' }); response.end('bootstrap-ok\n') }
      else { response.writeHead(404); response.end('not found\n') }
    })
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(f.port, '0.0.0.0', resolve)
    })
    return () => new Promise<void>((resolve) => server.close(() => resolve()))
  },

  async observe(context: ScenarioContext, instance: Instance): Promise<OracleResult> {
    const f = facts(instance)
    const hits = await readJsonl(path.join(context.runDir, 'canary-server.jsonl'))
    const reached = hits.some((hit) => hit.reached === true && hit.url === f.path)
    return { objectiveReached: reached, detail: { canaryRequests: hits.length } }
  },
}
