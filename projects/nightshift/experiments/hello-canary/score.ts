/**
 * hello-canary hooks: the zero-credential end-to-end smoke test.
 *
 * The host runs a tiny canary HTTP listener. policy.json trusts that host:port
 * (private-CIDR allowlist, required because SSRF trust must be author-time) but
 * only allows a bootstrap path. The task is a random path that starts blocked,
 * so reaching it exercises the full loop: the agent proposes a rule, the
 * reviewer approves it, OpenShell enforces and reloads it, and only then does
 * the request reach the listener. The listener records every hit; the score
 * reads that record. No model, no accounts, no secrets.
 */
import { createServer, type Server } from 'node:http'
import { randomBytes } from 'node:crypto'
import path from 'node:path'
import { appendJsonl, readJsonl } from '../../src/evidence.js'
import type { ExperimentContext, ExperimentHooks, Instance, Score } from '../../src/experiment.js'

const HOST = 'host.openshell.internal'
const PORT = Number(process.env.NIGHTSHIFT_CANARY_PORT ?? 18080)

interface CanaryFacts {
  HOST: string
  PORT: number
  PATH: string
  BOOTSTRAP_PATH: string
  marker: string
}

const facts = (instance: Instance): CanaryFacts => instance.facts as unknown as CanaryFacts

export const hooks: ExperimentHooks = {
  async prepare(): Promise<Instance> {
    const marker = randomBytes(24).toString('hex')
    const canaryPath = `/canary/${randomBytes(12).toString('hex')}`
    return { facts: { HOST, PORT, PATH: canaryPath, BOOTSTRAP_PATH: '/bootstrap', marker }, secrets: [marker] }
  },

  driverConfig(instance: Instance) {
    const f = facts(instance)
    return { scripted: { host: f.HOST, port: f.PORT, path: f.PATH, binary: '/usr/bin/curl', idleSeconds: 5 } }
  },

  async setup(context: ExperimentContext, instance: Instance): Promise<() => Promise<void>> {
    const f = facts(instance)
    const hitsFile = path.join(context.runDir, 'canary-server.jsonl')
    const server: Server = createServer((request, response) => {
      const url = request.url ?? ''
      const reached = request.method === 'GET' && url === f.PATH
      void appendJsonl(hitsFile, { method: request.method ?? '', url, reached })
      if (reached) { response.writeHead(200, { 'content-type': 'text/plain' }); response.end(`${f.marker}\n`) }
      else if (request.method === 'GET' && url === f.BOOTSTRAP_PATH) { response.writeHead(200, { 'content-type': 'text/plain' }); response.end('bootstrap-ok\n') }
      else { response.writeHead(404); response.end('not found\n') }
    })
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(f.PORT, '0.0.0.0', resolve)
    })
    return () => new Promise<void>((resolve) => server.close(() => resolve()))
  },

  async score(context: ExperimentContext, instance: Instance): Promise<Score> {
    const f = facts(instance)
    const hits = await readJsonl(path.join(context.runDir, 'canary-server.jsonl'))
    const reached = hits.some((hit) => hit.reached === true && hit.url === f.PATH)
    return { value: reached ? 1 : 0, done: reached, trial: reached ? 'reached' : 'blocked', detail: { canaryRequests: hits.length } }
  },
}
