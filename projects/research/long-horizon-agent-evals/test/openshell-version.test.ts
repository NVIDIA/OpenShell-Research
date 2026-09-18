import assert from 'node:assert/strict'
import test from 'node:test'
import { assertMatchingOpenShellVersions } from '../src/openshell-version.js'

test('accepts matching supported OpenShell releases', () => {
  assert.doesNotThrow(() => assertMatchingOpenShellVersions('0.0.106', '0.0.106'))
  assert.doesNotThrow(() => assertMatchingOpenShellVersions('v0.0.115', '0.0.115'))
  assert.doesNotThrow(() => assertMatchingOpenShellVersions('0.0.115+build.1', '0.0.115'))
})

test('rejects OpenShell releases below the minimum', () => {
  assert.throws(
    () => assertMatchingOpenShellVersions('0.0.105', '0.0.105'),
    /gateway 0\.0\.105 is older than required 0\.0\.106/,
  )
})

test('rejects mismatched gateway and SDK releases', () => {
  assert.throws(
    () => assertMatchingOpenShellVersions('0.0.115', '0.0.106'),
    /gateway 0\.0\.115 and TypeScript SDK 0\.0\.106 must use the same release/,
  )
})

test('rejects non-release OpenShell versions', () => {
  assert.throws(
    () => assertMatchingOpenShellVersions('development', '0.0.106'),
    /gateway reported an unsupported version/,
  )
})
