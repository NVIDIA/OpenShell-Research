import assert from 'node:assert/strict'
import test from 'node:test'
import {
  defaultHandoffOptions,
  trimHandoff,
  type HandoffEntry,
} from '../src/handoff.js'

const command = (command: string): HandoffEntry => ({
  type: 'command_execution',
  command,
  output: '',
  exitCode: 0,
})

const message = (text: string): HandoffEntry => ({ type: 'agent_message', text })
const reasoning = (text: string): HandoffEntry => ({ type: 'reasoning', text })

function serializedCharacters(entries: readonly HandoffEntry[]): number {
  return entries.reduce((total, entry) => total + JSON.stringify(entry).length + 1, 0)
}

test('repeated prose is normalized and only its most recent occurrence is kept', () => {
  const entries = [
    reasoning('  No approved   mechanism remains. '),
    command('first attempt'),
    message('no APPROVED mechanism REMAINS.'),
    reasoning('different observation'),
  ]

  assert.deepEqual(trimHandoff(entries), [
    command('first attempt'),
    message('no APPROVED mechanism REMAINS.'),
    reasoning('different observation'),
  ])
})

test('blank prose entries are never duplicates of each other', () => {
  assert.deepEqual(trimHandoff([reasoning(''), message('   ')]), [reasoning(''), message('   ')])
})

test('the entry budget evicts the oldest prose before an older command', () => {
  const entries = [command('old command'), message('old prose'), command('new command'), reasoning('new prose')]
  const trimmed = trimHandoff(entries, { maxEntries: 3, maxCharacters: 10_000 })

  assert.deepEqual(trimmed, [command('old command'), command('new command'), reasoning('new prose')])
})

test('the character budget evicts prose before commands', () => {
  const commands = [command('old command'), command('new command')]
  const maxCharacters = serializedCharacters(commands)
  const trimmed = trimHandoff([commands[0]!, message('short prose'), commands[1]!], {
    maxEntries: 32,
    maxCharacters,
  })

  assert.deepEqual(trimmed, commands)
  assert.ok(serializedCharacters(trimmed) <= maxCharacters)
})

test('the oldest command is evicted when only commands remain', () => {
  assert.deepEqual(
    trimHandoff([command('one'), command('two'), command('three')], {
      maxEntries: 2,
      maxCharacters: 10_000,
    }),
    [command('two'), command('three')],
  )
})

test('both default handoff budgets are enforced', () => {
  const entries = Array.from({ length: 40 }, (_, index) => command(`attempt ${index}`))
  const trimmed = trimHandoff(entries)

  assert.equal(trimmed.length, defaultHandoffOptions.maxEntries)
  assert.ok(serializedCharacters(trimmed) <= defaultHandoffOptions.maxCharacters)
  assert.deepEqual(trimmed, entries.slice(-defaultHandoffOptions.maxEntries))
})

test('an entry larger than the character budget is dropped', () => {
  assert.deepEqual(trimHandoff([message('too large')], { maxEntries: 32, maxCharacters: 1 }), [])
})
