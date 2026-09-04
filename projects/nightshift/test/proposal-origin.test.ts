import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isMechanisticRationale } from '../src/openshell.js'

// Rationales observed on OpenShell 0.0.116 in hello-canary runs.
test('the mechanistic rationale template is recognized', () => {
  assert.equal(isMechanisticRationale('Allow codex to connect to chatgpt.com:443 (HTTPS).'), true)
  assert.equal(isMechanisticRationale('Allow git-remote-http to connect to github.com:443 (HTTPS).'), true)
  assert.equal(isMechanisticRationale('Allow curl to connect to host.openshell.internal:18080.'), true)
})

test('an agent-authored intent summary is not mistaken for the template', () => {
  assert.equal(isMechanisticRationale('Allow the assigned GET request.'), false)
  assert.equal(isMechanisticRationale('Allow /usr/bin/curl to GET /canary/abc on host.openshell.internal:18080 for the assigned task.'), false)
  assert.equal(isMechanisticRationale(''), false)
})
