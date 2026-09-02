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

async function githubJson(token: string, url: string, init: RequestInit = {}): Promise<{
  status: number
  body: unknown
  headers: Headers
}> {
  const response = await fetch(url, { ...init, headers: { ...headers(token), ...(init.headers ?? {}) } })
  return { status: response.status, body: await response.json().catch(() => ({})), headers: response.headers }
}

export async function getGithubRepositoryState(token: string, owner: string, repo: string): Promise<GithubRepositoryState> {
  const base = repositoryUrl(owner, repo)
  const repository = await githubJson(token, base)
  if (repository.status === 404) return { exists: false }
  if (repository.status !== 200) throw new Error(`GitHub repository check returned HTTP ${repository.status}`)
  const repoBody = repository.body as { default_branch?: string }
  const refsFor = async (namespace: 'heads' | 'tags'): Promise<Record<string, string>> => {
    const refs: Array<{ ref?: string; object?: { sha?: string } }> = []
    for (let page = 1; ; page += 1) {
      const result = await githubJson(token, `${base}/git/matching-refs/${namespace}/?per_page=100&page=${page}`)
      if (result.status !== 200) throw new Error(`GitHub ${namespace} check returned HTTP ${result.status}`)
      const batch = (result.body as typeof refs) ?? []
      refs.push(...batch)
      if (!result.headers.get('link')?.includes('rel="next"')) break
    }
    return Object.fromEntries(refs
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
