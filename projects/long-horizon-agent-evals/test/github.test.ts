import assert from 'node:assert/strict'
import test from 'node:test'
import { getGithubRepositoryState } from '../src/github.js'

test('repository snapshots include every page of refs', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (input) => {
    const url = String(input)
    if (url.endsWith('/repos/example/repo')) {
      return Response.json({ default_branch: 'main' })
    }
    const namespace = url.includes('/heads/') ? 'heads' : 'tags'
    const page = new URL(url).searchParams.get('page')
    const refs = page === '1'
      ? Array.from({ length: 100 }, (_, index) => ({
          ref: `refs/${namespace}/page-one-${index}`,
          object: { sha: `sha-${namespace}-one-${index}` },
        }))
      : [{ ref: `refs/${namespace}/page-two`, object: { sha: `sha-${namespace}-two` } }]
    return Response.json(refs, {
      headers: page === '1' ? { link: '<https://api.github.com/next>; rel="next"' } : {},
    })
  }

  try {
    const state = await getGithubRepositoryState('token', 'example', 'repo')
    assert.equal(Object.keys(state.heads ?? {}).length, 101)
    assert.equal(Object.keys(state.tags ?? {}).length, 101)
    assert.equal(state.heads?.['refs/heads/page-two'], 'sha-heads-two')
  } finally {
    globalThis.fetch = originalFetch
  }
})
