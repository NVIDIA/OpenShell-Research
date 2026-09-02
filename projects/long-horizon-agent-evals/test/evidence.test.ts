import assert from 'node:assert/strict'
import { test } from 'node:test'
import { redact } from '../src/evidence.js'

test('known secrets are replaced wherever they appear', () => {
  const marker = 'a1b2c3d4e5f6'
  const text = `GET /canary -> ${marker}\n{"body":"${marker}"}`
  const result = redact(text, [marker])
  assert.ok(!result.includes(marker))
  assert.equal(result.split('[redacted]').length - 1, 2)
})

test('well-known credential shapes are redacted without being listed', () => {
  const text = [
    'Authorization: Bearer github_pat_11ABCDEFG0123456789_abcdefghijklmnop',
    'token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef012345',
    'jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123',
    'https://example.test/api?access_token=secret-value&x=1',
  ].join('\n')
  const result = redact(text, [])
  assert.ok(!result.includes('github_pat_11'))
  assert.ok(!result.includes('ghp_ABC'))
  assert.ok(!result.includes('eyJhbGci'))
  assert.ok(!result.includes('secret-value'))
  assert.ok(result.includes('access_token=[redacted-query-token]&x=1'))
})

test('empty secrets are ignored rather than matching everything', () => {
  assert.equal(redact('plain text', ['']), 'plain text')
})
