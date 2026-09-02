import assert from 'node:assert/strict'
import { test } from 'node:test'
import { assertMatchingOpenShellVersions, minimumOpenShellVersion } from '../src/openshell.js'

test('matching gateway and SDK releases at or above the minimum are accepted', () => {
  assert.doesNotThrow(() => assertMatchingOpenShellVersions(minimumOpenShellVersion, `v${minimumOpenShellVersion}`))
})

test('a gateway older than the minimum is rejected', () => {
  assert.throws(() => assertMatchingOpenShellVersions('0.0.1', minimumOpenShellVersion), /older than required/)
})

test('a gateway and SDK from different releases are rejected with the install hint', () => {
  assert.throws(() => assertMatchingOpenShellVersions('0.0.117', '0.0.116'), /install @nvidia\/openshell-sdk@0\.0\.117/)
})

test('an unparseable version is rejected rather than compared', () => {
  assert.throws(() => assertMatchingOpenShellVersions('dev', '0.0.116'), /unsupported version/)
})
