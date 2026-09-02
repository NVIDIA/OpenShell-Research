import assert from 'node:assert/strict'
import test from 'node:test'
import { campaignRuntimeOptions, scaleRuntimeOptions } from '../src/runtime-options.js'

test('campaign reads minutes from the CLI', () => {
  assert.deepEqual(campaignRuntimeOptions(['--minutes', '45'], { LAB_DURATION_MINUTES: '30' }), { minutes: 45 })
})

test('runtime values fall back to the environment', () => {
  assert.deepEqual(campaignRuntimeOptions([], { LAB_DURATION_MINUTES: '15' }), { minutes: 15 })
  assert.deepEqual(scaleRuntimeOptions([], {
    LAB_DURATION_MINUTES: '20',
    LAB_RUNS: '50',
    LAB_CONCURRENCY: '2',
  }), { minutes: 20, runs: 50, concurrency: 2 })
})

test('runtime values have simple defaults', () => {
  assert.deepEqual(campaignRuntimeOptions([], {}), { minutes: 30 })
  assert.deepEqual(scaleRuntimeOptions([], {}), { minutes: 30, runs: 50, concurrency: 2 })
})

test('CLI values override scale environment defaults', () => {
  assert.deepEqual(scaleRuntimeOptions([
    '--minutes', '30',
    '--runs', '8',
    '--concurrency', '4',
  ], {
    LAB_DURATION_MINUTES: '10',
    LAB_RUNS: '3',
    LAB_CONCURRENCY: '1',
  }), { minutes: 30, runs: 8, concurrency: 4 })
})

test('runtime values must be positive integers', () => {
  assert.throws(() => campaignRuntimeOptions(['--minutes', '0'], {}), /positive integer/)
  assert.throws(() => scaleRuntimeOptions(['--runs', '1.5'], {}), /positive integer/)
  assert.throws(() => scaleRuntimeOptions([], { LAB_CONCURRENCY: 'many' }), /positive integer/)
})

test('model and endpoint settings are not command-line options', () => {
  assert.throws(() => campaignRuntimeOptions(['--model', 'example'], {}), /Unknown option/)
  assert.throws(() => scaleRuntimeOptions(['--reviewer-endpoint', 'https:\/\/example.test'], {}), /Unknown option/)
})
