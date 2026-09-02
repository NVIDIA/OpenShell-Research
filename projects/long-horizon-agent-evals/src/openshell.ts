/**
 * The only module that imports the OpenShell SDK.
 *
 * Everything the lab needs from OpenShell is expressed here as a small set of
 * plain functions: connect, run a sandbox, inject credentials, read and decide
 * policy proposals, and collect evidence. When an OpenShell release changes an
 * RPC, this is the one file to update.
 */
import { readFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { OpenShellClient, type ConnectOptions, type SandboxSpec } from '@nvidia/openshell-sdk'

export const minimumOpenShellVersion = '0.0.116'

/** Create-time sandbox policy in the SDK's JSON shape (camelCase proto fields). */
export type Policy = NonNullable<SandboxSpec['policy']>

export interface Gateway {
  client: OpenShellClient
  endpoint: string
  workspace: string
  version: string
  sdkVersion: string
}

export interface ProviderSpec {
  /** Provider record name; unique per workspace. */
  name: string
  /** Provider profile id, for example `github` or `openai`. */
  type: string
  credentials: Record<string, string>
  config?: Record<string, string>
}

export interface SandboxRequest {
  name: string
  image: string
  policy: Policy
  providers: string[]
  labels: Record<string, string>
  /** Sandbox-scoped settings applied after creation. */
  settings: Record<string, string | boolean>
}

export interface Proposal {
  id: string
  status: string
  stage: string
  ruleName: string
  rationale: string
  securityNotes: string
  confidence: number
  binary: string
  validationResult: string
  applicationError: string
  rejectionReason: string
  reviewToken: string
  createdAtMs: number
  currentEffectivePolicyHash: string
  candidateEffectivePolicyHash: string
  proposedRule: unknown
  currentEffectivePolicy: unknown
  candidateEffectivePolicy: unknown
}

export interface Decision {
  decision: 'approve' | 'reject'
  reason: string
}

export type Application =
  | 'applied'
  | 'review_stale_retry'
  | 'rejection_already_satisfied'
  | 'approval_failed_then_rejected'
  | 'failed'

export interface AppliedDecision extends Decision {
  effectiveDecision: 'approve' | 'reject' | 'pending'
  application: Application
  applicationError?: string
  fallbackApplicationError?: string
  policyVersion?: number
}

export interface PolicyStatus {
  activeVersion: number
  revision?: { version: number; status: number; loadError: string }
}

export interface PolicyReloadFailure {
  version: number
  activeVersion: number
  loadError: string
}

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))

/**
 * Connect to a gateway. Resolution order: explicit endpoint, then the
 * `LAB_OPENSHELL_GATEWAY` / `OPENSHELL_GATEWAY_ENDPOINT` environment, then the
 * OpenShell CLI's active local gateway and its mTLS material. This means a
 * fresh `openshell` install needs no gateway configuration in `.env`.
 */
export async function connectGateway(options: { endpoint?: string; workspace?: string } = {}): Promise<Gateway> {
  const workspace = options.workspace ?? process.env.LAB_WORKSPACE ?? 'default'
  const connect = await resolveConnectOptions(options.endpoint)
  const client = await OpenShellClient.connect(connect)
  const health = await client.health()
  if (!health.version) throw new Error('OpenShell gateway did not report a version')
  const sdkVersion = await installedSdkVersion()
  assertMatchingOpenShellVersions(health.version, sdkVersion)
  return { client, endpoint: connect.gateway, workspace, version: health.version, sdkVersion }
}

export async function resolveConnectOptions(endpoint?: string): Promise<ConnectOptions> {
  const explicit = endpoint ?? process.env.LAB_OPENSHELL_GATEWAY ?? process.env.OPENSHELL_GATEWAY_ENDPOINT
  const optionalFile = async (name: string): Promise<Buffer | undefined> => {
    const file = process.env[name]
    return file ? readFile(file) : undefined
  }
  if (explicit) {
    return {
      gateway: explicit,
      caCert: await optionalFile('OPENSHELL_CA_CERT'),
      clientCert: await optionalFile('OPENSHELL_CLIENT_CERT'),
      clientKey: await optionalFile('OPENSHELL_CLIENT_KEY'),
      oidcToken: process.env.OPENSHELL_TOKEN || undefined,
      insecureSkipVerify: process.env.OPENSHELL_INSECURE === '1',
    }
  }
  const local = await localGatewayFromCli()
  if (!local) {
    throw new Error('no OpenShell gateway configured: set LAB_OPENSHELL_GATEWAY or run `openshell gateway add ... --local`')
  }
  return local
}

async function localGatewayFromCli(): Promise<ConnectOptions | undefined> {
  const configDir = path.join(homedir(), '.config', 'openshell')
  const name = (await readFile(path.join(configDir, 'active_gateway'), 'utf8').catch(() => '')).trim()
  if (!name) return undefined
  const gatewayDir = path.join(configDir, 'gateways', name)
  const metadata = JSON.parse(await readFile(path.join(gatewayDir, 'metadata.json'), 'utf8')) as {
    gateway_endpoint?: string
    auth_mode?: string
  }
  if (!metadata.gateway_endpoint) return undefined
  const options: ConnectOptions = { gateway: metadata.gateway_endpoint }
  if (metadata.auth_mode === 'mtls') {
    const mtls = path.join(gatewayDir, 'mtls')
    options.caCert = await readFile(path.join(mtls, 'ca.crt'))
    options.clientCert = await readFile(path.join(mtls, 'tls.crt'))
    options.clientKey = await readFile(path.join(mtls, 'tls.key'))
  }
  return options
}

export async function installedSdkVersion(): Promise<string> {
  const file = path.join(root, 'node_modules', '@nvidia', 'openshell-sdk', 'package.json')
  const pkg = JSON.parse(await readFile(file, 'utf8')) as { version?: string }
  if (!pkg.version) throw new Error('OpenShell TypeScript SDK did not report a version')
  return pkg.version
}

type Version = [number, number, number]

function parseVersion(value: string, label: string): Version {
  const match = value.trim().match(/^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/)
  if (!match) throw new Error(`${label} reported an unsupported version: ${value}`)
  return [Number(match[1]), Number(match[2]), Number(match[3])]
}

function compareVersions(left: Version, right: Version): number {
  for (const difference of [left[0] - right[0], left[1] - right[1], left[2] - right[2]]) {
    if (difference !== 0) return difference
  }
  return 0
}

/**
 * The SDK's generated RPC types track one OpenShell release. Require the same
 * release on both sides so a silent contract drift cannot corrupt evidence.
 */
export function assertMatchingOpenShellVersions(gatewayValue: string, sdkValue: string): void {
  const gateway = parseVersion(gatewayValue, 'OpenShell gateway')
  const sdk = parseVersion(sdkValue, 'OpenShell TypeScript SDK')
  const minimum = parseVersion(minimumOpenShellVersion, 'minimum OpenShell version')
  if (compareVersions(gateway, minimum) < 0) {
    throw new Error(`OpenShell gateway ${gateway.join('.')} is older than required ${minimumOpenShellVersion}`)
  }
  if (compareVersions(sdk, minimum) < 0) {
    throw new Error(`OpenShell TypeScript SDK ${sdk.join('.')} is older than required ${minimumOpenShellVersion}`)
  }
  if (compareVersions(gateway, sdk) !== 0) {
    throw new Error(
      `OpenShell gateway ${gateway.join('.')} and TypeScript SDK ${sdk.join('.')} must use the same release; `
      + `install @nvidia/openshell-sdk@${gateway.join('.')}`,
    )
  }
}

// ---------------------------------------------------------------------------
// Providers: OpenShell injects credentials into the sandbox as placeholders and
// substitutes the real value only at the network boundary.

export async function ensureProviders(gateway: Gateway, providers: ProviderSpec[]): Promise<void> {
  const { client, workspace } = gateway
  for (const provider of providers) {
    await client.raw.createProvider({
      workspace,
      provider: {
        metadata: { name: provider.name, workspace },
        type: provider.type,
        credentials: provider.credentials,
        ...(provider.config ? { config: provider.config } : {}),
      },
    })
  }
}

async function existingProviderNames(gateway: Gateway): Promise<Set<string>> {
  const response = await gateway.client.raw.listProviders({ limit: 1000, offset: 0, workspace: gateway.workspace, allWorkspaces: false })
  return new Set(response.providers.flatMap((provider) => provider.metadata?.name ? [provider.metadata.name] : []))
}

// ---------------------------------------------------------------------------
// Model egress: a model-driven runtime needs to reach its inference endpoint.
// The scenario policy stays model-agnostic; the harness adds this egress rule
// for the configured endpoint when a model runtime is used.

export function withModelEgress(policy: Policy, baseUrl: string): Policy {
  const url = new URL(baseUrl)
  const port = url.port ? Number(url.port) : url.protocol === 'https:' ? 443 : 80
  const base = policy as Record<string, unknown>
  const networkPolicies = { ...((base.networkPolicies as Record<string, unknown>) ?? {}) }
  networkPolicies.model = {
    name: 'model',
    endpoints: [{ host: url.hostname, port, protocol: 'rest', enforcement: 'enforce', access: 'full' }],
    binaries: [{ path: '/usr/bin/node' }],
  }
  return { ...base, networkPolicies } as Policy
}

// ---------------------------------------------------------------------------
// Sandboxes

export async function createSandbox(gateway: Gateway, request: SandboxRequest): Promise<{ id: string; name: string }> {
  const { client } = gateway
  const ref = await client.sandbox.create({
    name: request.name,
    image: request.image,
    labels: request.labels,
    providers: request.providers,
    policy: request.policy,
    command: ['sleep', 'infinity'],
  })
  await client.sandbox.waitReady(ref.name, 180)
  for (const [key, value] of Object.entries(request.settings)) {
    await client.sandbox.setSetting(ref.name, key, {
      value: typeof value === 'boolean' ? { case: 'boolValue', value } : { case: 'stringValue', value },
    })
  }
  return { id: ref.id, name: ref.name }
}

export interface ExecOptions {
  stdin?: Buffer
  environment?: Record<string, string>
  timeoutSecs?: number
  signal?: AbortSignal
}

export async function exec(gateway: Gateway, sandbox: string, command: string[], options: ExecOptions = {}): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  const result = await gateway.client.sandbox.exec(sandbox, command, options)
  return { exitCode: result.exitCode, stdout: result.stdout.toString('utf8'), stderr: result.stderr.toString('utf8') }
}

export type ExecEvent = { stream: 'stdout' | 'stderr'; data: Buffer } | { type: 'exit'; exitCode: number }

export function execStream(gateway: Gateway, sandbox: string, command: string[], options: ExecOptions = {}): AsyncIterable<ExecEvent> {
  return gateway.client.sandbox.execStream(sandbox, command, options)
}

/** Wait until the in-sandbox policy API answers, which happens a few seconds after enabling proposals. */
export async function waitForPolicyApi(gateway: Gateway, sandbox: string, timeoutMs = 60_000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const result = await exec(gateway, sandbox, ['/usr/bin/curl', '-sS', '-m', '5', '-o', '/dev/null', '-w', '%{http_code}', 'http://policy.local/v1/policy/current'], { timeoutSecs: 20 })
    if (result.stdout.trim() === '200') return
    await delay(1000)
  }
  throw new Error(`policy.local did not become available in ${sandbox} within ${timeoutMs / 1000}s`)
}

export async function effectivePolicy(gateway: Gateway, sandbox: string): Promise<unknown> {
  return gateway.client.sandbox.getConfig(sandbox)
}

export async function policyStatus(gateway: Gateway, sandbox: string): Promise<PolicyStatus> {
  const response = await gateway.client.raw.getSandboxPolicyStatus({ name: sandbox, version: 0, global: false, workspace: gateway.workspace })
  return {
    activeVersion: response.activeVersion,
    revision: response.revision
      ? { version: response.revision.version, status: response.revision.status, loadError: response.revision.loadError }
      : undefined,
  }
}

/** A failed reload means the sandbox is not enforcing the policy the gateway believes it is. */
export function policyReloadFailure(status: PolicyStatus): PolicyReloadFailure | undefined {
  if (status.revision?.status !== 3) return undefined
  return {
    version: status.revision.version,
    activeVersion: status.activeVersion,
    loadError: status.revision.loadError || 'OpenShell reported a failed policy reload',
  }
}

/**
 * Delete the sandbox and providers, then verify they are gone. Returns the
 * list of resources that still exist so the caller can mark the run invalid.
 */
export async function cleanup(gateway: Gateway, sandbox: string | undefined, providers: string[]): Promise<string[]> {
  const { client, workspace } = gateway
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (sandbox) {
      const existing = await client.sandbox.list().catch(() => [])
      if (existing.some((candidate) => candidate.name === sandbox)) {
        await client.sandbox.delete(sandbox).catch(() => undefined)
        await client.sandbox.waitDeleted(sandbox, 30).catch(() => undefined)
      }
    }
    const names = await existingProviderNames(gateway).catch(() => new Set(providers))
    for (const provider of providers) {
      if (names.has(provider)) await client.raw.deleteProvider({ name: provider, workspace }).catch(() => undefined)
    }
    if (attempt < 2) await delay(250)
  }
  const errors: string[] = []
  if (sandbox) {
    try {
      if ((await client.sandbox.list()).some((candidate) => candidate.name === sandbox)) errors.push(`sandbox still exists: ${sandbox}`)
    } catch (error) {
      errors.push(`could not verify sandbox deletion: ${message(error)}`)
    }
  }
  try {
    const names = await existingProviderNames(gateway)
    for (const provider of providers) if (names.has(provider)) errors.push(`provider still exists: ${provider}`)
  } catch (error) {
    errors.push(`could not verify provider deletion: ${message(error)}`)
  }
  return errors
}

// ---------------------------------------------------------------------------
// Proposals: the agent asks for policy; the adjudicator decides; this applies.

export async function proposals(gateway: Gateway, sandbox: string, status: 'pending' | 'all' = 'pending'): Promise<Proposal[]> {
  const response = await gateway.client.raw.getDraftPolicy({ name: sandbox, statusFilter: status === 'all' ? '' : status, workspace: gateway.workspace })
  return response.chunks
    .map((chunk) => ({
      id: chunk.id,
      status: chunk.status,
      stage: chunk.stage,
      ruleName: chunk.ruleName,
      rationale: chunk.rationale,
      securityNotes: chunk.securityNotes,
      confidence: chunk.confidence,
      binary: chunk.binary,
      validationResult: chunk.validationResult,
      applicationError: chunk.applicationError,
      rejectionReason: chunk.rejectionReason,
      reviewToken: chunk.reviewToken,
      createdAtMs: Number(chunk.createdAtMs),
      currentEffectivePolicyHash: chunk.currentEffectivePolicyHash,
      candidateEffectivePolicyHash: chunk.candidateEffectivePolicyHash,
      proposedRule: chunk.proposedRule,
      currentEffectivePolicy: chunk.currentEffectivePolicy,
      candidateEffectivePolicy: chunk.candidateEffectivePolicy,
    }))
    .sort((a, b) => a.createdAtMs - b.createdAtMs)
}

export async function proposalHistory(gateway: Gateway, sandbox: string): Promise<Array<{ timestampMs: number; eventType: string; description: string; chunkId: string }>> {
  const response = await gateway.client.raw.getDraftHistory({ name: sandbox, workspace: gateway.workspace })
  return response.entries.map((entry) => ({
    timestampMs: Number(entry.timestampMs),
    eventType: entry.eventType,
    description: entry.description,
    chunkId: entry.chunkId,
  }))
}

export async function rejectProposal(gateway: Gateway, sandbox: string, proposal: Proposal, reason: string): Promise<void> {
  await gateway.client.raw.rejectDraftChunk({ name: sandbox, chunkId: proposal.id, workspace: gateway.workspace, reason })
}

/**
 * Apply an adjudicator decision, failing closed. An approval the gateway
 * cannot apply becomes a rejection; a stale review token asks the caller to
 * refetch; an already-rejected proposal is treated as satisfied.
 */
export async function applyDecision(gateway: Gateway, sandbox: string, proposal: Proposal, decision: Decision): Promise<AppliedDecision> {
  const { client, workspace } = gateway
  if (decision.decision === 'approve') {
    try {
      const applied = await client.raw.approveDraftChunk({ name: sandbox, chunkId: proposal.id, workspace, reviewToken: proposal.reviewToken })
      return { ...decision, effectiveDecision: 'approve', application: 'applied', policyVersion: applied.policyVersion }
    } catch (error) {
      const applicationError = message(error)
      if (isProposalReviewStaleError(error)) {
        return { ...decision, effectiveDecision: 'pending', application: 'review_stale_retry', applicationError }
      }
      try {
        await client.raw.rejectDraftChunk({ name: sandbox, chunkId: proposal.id, workspace, reason: `Approval could not be applied by gateway validation; failed closed. ${applicationError}` })
        return { ...decision, effectiveDecision: 'reject', application: 'approval_failed_then_rejected', applicationError }
      } catch (fallbackError) {
        return { ...decision, effectiveDecision: 'pending', application: 'failed', applicationError, fallbackApplicationError: message(fallbackError) }
      }
    }
  }
  try {
    await client.raw.rejectDraftChunk({ name: sandbox, chunkId: proposal.id, workspace, reason: decision.reason })
    return { ...decision, effectiveDecision: 'reject', application: 'applied' }
  } catch (error) {
    const applicationError = message(error)
    if (isProposalAlreadyRejectedError(error)) {
      return { ...decision, effectiveDecision: 'reject', application: 'rejection_already_satisfied', applicationError }
    }
    return { ...decision, effectiveDecision: 'pending', application: 'failed', applicationError }
  }
}

export function isProposalReviewStaleError(error: unknown): boolean {
  const text = message(error)
  return [
    'proposal inputs changed; evaluation refreshed, refetch and review again',
    'review token does not match the fetched proposal; refetch and review again',
    'proposal inputs changed before persistence; refetch and review again',
  ].some((fragment) => text.includes(fragment))
}

export function isProposalAlreadyRejectedError(error: unknown): boolean {
  return message(error).includes("chunk status is 'rejected', expected 'pending' or 'approved'")
}

/** Gateway-side candidate validation failure; such a proposal can never be approved. */
export function proposalPreflightError(proposal: { applicationError?: unknown }): string | undefined {
  if (typeof proposal.applicationError !== 'string') return undefined
  return proposal.applicationError.trim() || undefined
}

// ---------------------------------------------------------------------------

export function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export async function delay(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms))
}
