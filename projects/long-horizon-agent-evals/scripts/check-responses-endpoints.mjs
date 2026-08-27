#!/usr/bin/env node

import { readFile } from 'node:fs/promises'

function parseEnv(text) {
  const values = {}
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/)
    if (!match) continue
    let value = match[2].trim()
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1)
    }
    values[match[1]] = value
  }
  return values
}

function outputText(body) {
  return (body.output ?? [])
    .flatMap((item) => item.content ?? [])
    .filter((item) => item.type === 'output_text' && typeof item.text === 'string')
    .map((item) => item.text)
    .join('\n')
}

function validReviewDecision(text) {
  if (!text) return false
  try {
    const value = JSON.parse(text)
    return (value.decision === 'approve' || value.decision === 'reject')
      && typeof value.reason === 'string'
      && value.reason.length > 0
  } catch {
    return false
  }
}

const fileEnv = parseEnv(await readFile('.env', 'utf8').catch(() => ''))
const env = { ...fileEnv, ...Object.fromEntries(Object.entries(process.env).filter(([, value]) => value !== undefined)) }

async function check(role, reviewerContract) {
  const prefix = `LAB_${role.toUpperCase()}`
  const fallbackPrefix = role === 'reviewer' ? 'LAB_CHALLENGER' : prefix
  const apiKey = env[`${prefix}_API_KEY`] || env[`${fallbackPrefix}_API_KEY`]
  const url = env[`${prefix}_RESPONSES_URL`] || env[`${fallbackPrefix}_RESPONSES_URL`]
  const model = env[`${prefix}_MODEL`] || env[`${fallbackPrefix}_MODEL`]
  const reasoning = env[`${prefix}_REASONING`]
    || (role === 'reviewer' ? 'medium' : env[`${fallbackPrefix}_REASONING`] || 'medium')
  if (!apiKey || !url || !model) throw new Error(`Configure ${prefix}_API_KEY, ${prefix}_RESPONSES_URL, and ${prefix}_MODEL in .env`)

  const request = reviewerContract
    ? {
        model,
        input: [{ role: 'user', content: 'A request asks for read-only access to public documentation. Return an approval decision.' }],
        reasoning: { effort: reasoning, summary: 'detailed' },
        text: {
          format: {
            type: 'json_schema',
            name: 'review_decision',
            strict: true,
            schema: {
              type: 'object',
              additionalProperties: false,
              required: ['decision', 'reason'],
              properties: {
                decision: { type: 'string', enum: ['approve', 'reject'] },
                reason: { type: 'string' },
              },
            },
          },
        },
        max_output_tokens: 512,
      }
    : {
        model,
        input: [{ role: 'user', content: 'Reply with exactly: endpoint-ok' }],
        max_output_tokens: 128,
      }
  const started = Date.now()
  const response = await fetch(url, {
    method: 'POST',
    headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
  const body = await response.json().catch(() => ({}))
  const text = outputText(body).trim() || null
  const contractValid = reviewerContract ? validReviewDecision(text) : text === 'endpoint-ok'
  const result = {
    role,
    ok: response.ok && contractValid,
    httpStatus: response.status,
    elapsedMs: Date.now() - started,
    requestedModel: model,
    returnedModel: body.model ?? null,
    responseStatus: body.status ?? null,
    ...(reviewerContract ? { contractValid } : {}),
    text,
    usage: body.usage ?? null,
    error: body.error ? { type: body.error.type ?? null, code: body.error.code ?? null, message: body.error.message ?? null } : null,
  }
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`)
  return result.ok
}

const challengerOk = await check('challenger', false)
const reviewerOk = await check('reviewer', true)
if (!challengerOk || !reviewerOk) process.exitCode = 1
