import { randomBytes } from 'node:crypto'
import { loadEnv, required } from './common.js'
import { createGithubBranch, getGithubFile, putGithubFile } from './github.js'

async function main(): Promise<void> {
  await loadEnv()
  const owner = required('LAB_GITHUB_OWNER')
  const repo = required('LAB_GITHUB_REPO')
  const token = required('LAB_GITHUB_TOKEN')
  const sourceBranch = process.env.LAB_GITHUB_BRANCH ?? 'main'
  const suffix = randomBytes(8).toString('hex')
  const branch = `preflight/${suffix}`
  const file = `artifacts/${suffix}.txt`
  const content = `scoped-pat-write-confirmed:${suffix}`

  await createGithubBranch(token, owner, repo, branch, sourceBranch)
  const commitSha = await putGithubFile(token, owner, repo, file, branch, content, 'test: verify scoped token write access')
  const result = await getGithubFile(token, owner, repo, file, branch)
  if (!result.exists || result.content !== content) throw new Error('GitHub write verification failed')

  process.stdout.write(`${JSON.stringify({ ok: true, repository: `${owner}/${repo}`, branch, file, commitSha })}\n`)
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`)
  process.exitCode = 1
})
