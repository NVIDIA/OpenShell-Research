import assert from 'node:assert/strict'
import test from 'node:test'
import { compactReviewerHistory, isContextLengthExceeded, type ReviewerState } from '../src/reviewer-model.js'

function state(contents: string[]): ReviewerState {
  return {
    history: contents.map((content, index) => ({ role: index % 2 === 0 ? 'user' : 'assistant', content })),
  }
}

test('compacts complete oldest reviewer exchanges by message and character budgets', () => {
  const byMessages = state(['user-1', 'assistant-1', 'user-2', 'assistant-2', 'user-3', 'assistant-3'])
  assert.equal(compactReviewerHistory(byMessages, 4, 10_000), 2)
  assert.deepEqual(byMessages.history.map((message) => message.content), [
    'user-2', 'assistant-2', 'user-3', 'assistant-3',
  ])

  const byCharacters = state(['12345', '12345', '12', '12'])
  assert.equal(compactReviewerHistory(byCharacters, 10, 5), 2)
  assert.deepEqual(byCharacters.history.map((message) => message.content), ['12', '12'])
})

test('recognizes only context-limit bad requests', () => {
  assert.equal(isContextLengthExceeded(400, {
    error: { code: 'context_length_exceeded', message: 'input exceeds the context window' },
  }), true)
  assert.equal(isContextLengthExceeded(429, {
    error: { code: 'context_length_exceeded', message: 'input exceeds the context window' },
  }), false)
  assert.equal(isContextLengthExceeded(400, {
    error: { code: 'invalid_request', message: 'schema is invalid' },
  }), false)
})
