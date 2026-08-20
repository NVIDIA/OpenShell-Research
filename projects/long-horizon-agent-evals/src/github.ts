export interface GithubFileResult {
  exists: boolean
  content?: string
  sha?: string
}

export interface GithubRepositoryState {
  exists: boolean
  defaultBranch?: string
  heads?: Record<string, string>
  tags?: Record<string, string>
}

function repositoryUrl(owner: string, repo: string): string {
  return `https://api.github.com/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`
}

function headers(token: string): Record<string, string> {
  return {
    Accept: 'application/vnd.github+json',
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
    'X-GitHub-Api-Version': '2022-11-28',
  }
}

async function githubJson(token: string, url: string, init: RequestInit = {}): Promise<{ status: number; body: unknown }> {
  const response = await fetch(url, { ...init, headers: { ...headers(token), ...(init.headers ?? {}) } })
  return { status: response.status, body: await response.json().catch(() => ({})) }
}

export async function getGithubRepositoryState(token: string, owner: string, repo: string): Promise<GithubRepositoryState> {
  const base = repositoryUrl(owner, repo)
  const repository = await githubJson(token, base)
  if (repository.status === 404) return { exists: false }
  if (repository.status !== 200) throw new Error(`GitHub repository check returned HTTP ${repository.status}`)
  const repoBody = repository.body as { default_branch?: string }
  const refsFor = async (namespace: 'heads' | 'tags'): Promise<Record<string, string>> => {
    const result = await githubJson(token, `${base}/git/matching-refs/${namespace}/`)
    if (result.status !== 200) throw new Error(`GitHub ${namespace} check returned HTTP ${result.status}`)
    return Object.fromEntries(((result.body as Array<{ ref?: string; object?: { sha?: string } }>) ?? [])
      .flatMap((item) => item.ref && item.object?.sha ? [[item.ref, item.object.sha] as [string, string]] : [])
      .sort(([a], [b]) => a.localeCompare(b)))
  }
  const [heads, tags] = await Promise.all([refsFor('heads'), refsFor('tags')])
  return { exists: true, defaultBranch: repoBody.default_branch, heads, tags }
}

export async function getGithubBranchSha(token: string, owner: string, repo: string, branch: string): Promise<string | undefined> {
  const result = await githubJson(token, `${repositoryUrl(owner, repo)}/git/ref/heads/${branch.split('/').map(encodeURIComponent).join('/')}`)
  if (result.status === 404) return undefined
  if (result.status !== 200) throw new Error(`GitHub branch check returned HTTP ${result.status}`)
  return (result.body as { object?: { sha?: string } }).object?.sha
}

export async function createGithubBranch(
  token: string,
  owner: string,
  repo: string,
  branch: string,
  sourceBranch: string,
): Promise<string> {
  const sourceSha = await getGithubBranchSha(token, owner, repo, sourceBranch)
  if (!sourceSha) throw new Error(`GitHub source branch does not exist: ${sourceBranch}`)
  const result = await githubJson(token, `${repositoryUrl(owner, repo)}/git/refs`, {
    method: 'POST',
    body: JSON.stringify({ ref: `refs/heads/${branch}`, sha: sourceSha }),
  })
  if (result.status !== 201) {
    const message = (result.body as { message?: string }).message ?? 'unknown error'
    throw new Error(`GitHub branch creation returned HTTP ${result.status}: ${message}`)
  }
  return sourceSha
}

export async function getGithubFile(token: string, owner: string, repo: string, file: string, branch: string): Promise<GithubFileResult> {
  const encodedPath = file.split('/').map(encodeURIComponent).join('/')
  const url = `${repositoryUrl(owner, repo)}/contents/${encodedPath}?ref=${encodeURIComponent(branch)}`
  const response = await fetch(url, { headers: headers(token) })
  if (response.status === 200) {
    const body = (await response.json()) as { content?: string; encoding?: string; sha?: string }
    const content = body.encoding === 'base64' && body.content
      ? Buffer.from(body.content.replace(/\n/g, ''), 'base64').toString('utf8')
      : undefined
    return { exists: true, content, sha: body.sha }
  }
  if (response.status === 404) return { exists: false }
  throw new Error(`GitHub target check returned HTTP ${response.status}`)
}

export async function putGithubFile(
  token: string,
  owner: string,
  repo: string,
  file: string,
  branch: string,
  content: string,
  message: string,
): Promise<string> {
  const encodedPath = file.split('/').map(encodeURIComponent).join('/')
  const result = await githubJson(token, `${repositoryUrl(owner, repo)}/contents/${encodedPath}`, {
    method: 'PUT',
    body: JSON.stringify({
      branch,
      message,
      content: Buffer.from(content).toString('base64'),
    }),
  })
  if (result.status !== 201) {
    const errorMessage = (result.body as { message?: string }).message ?? 'unknown error'
    throw new Error(`GitHub content write returned HTTP ${result.status}: ${errorMessage}`)
  }
  const sha = (result.body as { commit?: { sha?: string } }).commit?.sha
  if (!sha) throw new Error('GitHub content write did not return a commit SHA')
  return sha
}
