export type NoteSummary = {
  path: string
  title: string
  project: string
  tags: string[]
  source: Record<string, unknown>
}

export type NoteDetail = NoteSummary & { content: string }

export type ProjectSummary = {
  name: string
  note_count: number

  unconfirmed: boolean
}

export type Job = {
  id: string
  status: 'pending' | 'claimed' | 'done' | 'failed'
  agent: string
  device: string
  project: string
  project_inference: string
  created_at: string
  last_activity_at: string
  claimed_by: string | null
  attempts: number
  last_error: string | null
  summarizer: string | null
  note_path: string | null
  completed_at: string | null
}

export class APIError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export const isUnauthorized = (error: unknown) => error instanceof APIError && error.status === 401

type AccessToken = { access_token: string; token_type: 'bearer' }

let accessToken: string | null = null
let refreshing: Promise<boolean> | null = null

const describe = (detail: unknown): string => {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail))
    return detail.map((item: { msg?: unknown }) => (typeof item?.msg === 'string' ? item.msg : JSON.stringify(item))).join(' ')
  return JSON.stringify(detail)
}

async function failure(response: Response): Promise<APIError> {
  const detail = await response
    .clone()
    .json()
    .then((body: { detail?: unknown }) => describe(body.detail))
    .catch(() => '')
  return new APIError(response.status, detail ? `${response.status} ${detail}` : `${response.status} ${response.statusText}`)
}

const ensureCsrfCookie = () => fetch('/api/auth/csrf', { method: 'HEAD' })

function refreshAccessToken(): Promise<boolean> {
  refreshing ??= ensureCsrfCookie()
    .then(() => fetch('/api/auth/refresh'))
    .then(async (response) => {
      accessToken = response.ok ? ((await response.json()) as AccessToken).access_token : null
      return accessToken !== null
    })
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

async function request(method: string, path: string, body?: unknown, retry = true): Promise<Response> {
  const headers: Record<string, string> = body === undefined ? {} : { 'Content-Type': 'application/json' }
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`
  const response = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  if (response.status !== 401 || !retry || !(await refreshAccessToken())) return response
  return request(method, path, body, false)
}

async function get<T>(path: string): Promise<T> {
  const response = await request('GET', path)
  if (!response.ok) throw await failure(response)
  return (await response.json()) as T
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await request(method, path, body)
  if (!response.ok) throw await failure(response)
  return (response.status === 204 ? undefined : await response.json()) as T
}

async function me(): Promise<User | null> {
  const response = await request('GET', '/auth/me')
  if (response.status === 401) return null
  if (!response.ok) throw await failure(response)
  return (await response.json()) as User
}

async function login(credentials: { username: string; password: string }): Promise<User> {
  await ensureCsrfCookie()
  const response = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(credentials),
  })
  if (!response.ok) throw await failure(response)
  accessToken = ((await response.json()) as AccessToken).access_token
  return get<User>('/auth/me')
}

async function logout(): Promise<void> {
  const response = await fetch('/api/auth/logout', { method: 'POST' })
  if (!response.ok) throw await failure(response)
  accessToken = null
}

export const api = {
  me,
  login,
  logout,
  apiKeys: () => get<APIKey[]>('/auth/api-keys'),
  createAPIKey: (payload: { name: string; expires_in_days: number | null }) => send<IssuedAPIKey>('POST', '/auth/api-keys', payload),
  deleteAPIKey: (id: string) => send<void>('DELETE', `/auth/api-keys/${id}`),
  settings: () => get<RuntimeSetting>('/settings'),
  updateSettings: (patch: Partial<RuntimeSetting>) => send<RuntimeSetting>('PATCH', '/settings', patch),
  providers: () => get<LLMProvider[]>('/llm-providers'),
  createProvider: (provider: Partial<LLMProvider> & { api_key?: string }) => send<LLMProvider>('POST', '/llm-providers', provider),
  updateProvider: (id: string, patch: Partial<LLMProvider> & { api_key?: string }) => send<LLMProvider>('PATCH', `/llm-providers/${id}`, patch),
  deleteProvider: (id: string) => send<void>('DELETE', `/llm-providers/${id}`),
  testProvider: (id: string) => send<ProviderTestResult>('POST', `/llm-providers/${id}/test`),
  projects: () => get<ProjectSummary[]>('/wiki/projects'),
  notes: (project?: string) => get<NoteSummary[]>(`/wiki/notes${project ? `?project=${encodeURIComponent(project)}` : ''}`),
  note: (path: string) => get<NoteDetail>(`/wiki/notes/${path}`),
  search: (q: string) => get<NoteSummary[]>(`/wiki/search?q=${encodeURIComponent(q)}`),
  jobs: () => get<Job[]>('/jobs'),
}

export type User = {
  id: string
  username: string
  last_login_at: string | null
}

export type APIKey = {
  id: string
  name: string
  prefix: string
  created_at: string
  deleted_at: string | null
  last_used_at: string | null
}

export type IssuedAPIKey = APIKey & { key: string }

export type RuntimeSetting = {
  document_language: string
  job_max_attempts: number
  job_batch_size: number
  job_idle_seconds: number
  transcript_retention_hours: number
  stale_claim_hours: number
  maintenance_interval_seconds: number
  worker_poll_interval_seconds: number
  session_ttl_hours: number
  updated_at: string
}

export type ProviderTestResult = {
  ok: boolean
  latency_ms: number
  detail: string
}

export type LLMProvider = {
  id: string
  name: string
  kind: 'llama' | 'anthropic'
  model: string
  base_url: string | null
  context_tokens: number
  priority: number
  min_job_age_seconds: number
  connect_timeout_seconds: number
  timeout_seconds: number
  enabled: boolean
  has_api_key: boolean
  updated_at: string
}
