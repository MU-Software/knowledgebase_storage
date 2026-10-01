export type NoteSummary = {
  path: string
  title: string
  project: string
  tags: string[]
  source: Record<string, unknown>
}

export type NoteDetail = NoteSummary & { content: string }

export type ProjectNode = {
  name: string
  note_count: number
  unconfirmed: boolean
  aliases: string[]
  overview: string | null
}

export type PromptStage =
  | 'session_explore'
  | 'session_chunk'
  | 'session_final'
  | 'session_verify'
  | 'session_split'
  | 'project_overview'
  | 'project_link'
  | 'memory_merge'
  | 'note_relations'

export type Prompt = {
  id: string
  stage: PromptStage
  status: 'draft' | 'active' | 'archived'
  label: string
  system: string
  instruction: string
  thinking: boolean
  temperature: number
  max_tokens: number
  parent_id: string | null
  created_at: string
  updated_at: string
}

export type ProjectLink = {
  id: string
  kind: 'same' | 'part_of' | 'related'
  source: string
  target: string
  description: string
  stated_by_user: string
  confirmed: boolean
  unrelated: boolean
  summarizer: string
  created_at: string
}

export type NoteForget = {
  paths: string[]
  verdict: 'hide' | 'purge'
  reason: string
}

export type NoteForgotten = {
  path: string
  removed: boolean
  raw_removed: boolean
}

export type NoteMove = {
  path: string
  project: string
  first_request: number
  last_request: number
}

export type NoteMoved = {
  path: string
  project: string
  moved_to: string | null
}

export type ProjectEntry = {
  id: string
  slug: string
  description: string
  sources: { path: string; prefix: string }[]
  container: boolean
  updated_at: string
}

export type ProjectMergeResult = {
  project: string
  moved: number
  archived: string[]
  merging: string[]
}

export type ProjectDeleteResult = {
  project: string
  deleted_notes: number
}

export type Job = {
  id: string
  kind: 'session' | 'memory_merge' | 'project_overview' | 'project_suggestion' | 'note_relations'
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

export type JobPage = {
  items: Job[]
  total: number
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
  projects: () => get<ProjectNode[]>('/wiki/projects'),
  mergeProjects: (payload: { source: string; target: string }) => send<ProjectMergeResult>('POST', '/wiki/projects/merge', payload),
  deleteProject: (name: string) => send<ProjectDeleteResult>('DELETE', `/wiki/projects/${encodeURIComponent(name)}`),
  requestOverview: (name: string) => send<Job | null>('POST', `/wiki/projects/${encodeURIComponent(name)}/overview`),
  notes: (project?: string) => get<NoteSummary[]>(`/wiki/notes${project ? `?project=${encodeURIComponent(project)}` : ''}`),
  forgetNotes: (payload: NoteForget) => send<NoteForgotten[]>('POST', '/wiki/notes/forget', payload),
  moveNote: (payload: NoteMove) => send<NoteMoved>('POST', '/wiki/notes/move', payload),
  note: (path: string) => get<NoteDetail>(`/wiki/notes/${path}`),
  search: (q: string) => get<NoteSummary[]>(`/wiki/search?q=${encodeURIComponent(q)}`),
  jobs: ({ offset, limit }: { offset: number; limit: number }) => get<JobPage>(`/jobs?offset=${offset}&limit=${limit}`),
  prompts: () => get<Prompt[]>('/prompts'),
  draftPrompt: (payload: Omit<Prompt, 'id' | 'status' | 'parent_id' | 'created_at' | 'updated_at'>) => send<Prompt>('POST', '/prompts', payload),
  amendPrompt: ({ id, patch }: { id: string; patch: Partial<Prompt> }) => send<Prompt>('PATCH', `/prompts/${id}`, patch),
  activatePrompt: (id: string) => send<Prompt>('POST', `/prompts/${id}/activate`),
  links: () => get<ProjectLink[]>('/wiki/links'),
  writeLink: (payload: Partial<ProjectLink>) => send<ProjectLink>('PUT', '/wiki/links', payload),
  applyLink: ({ source, target, reverse = false }: { source: string; target: string; reverse?: boolean }) =>
    send<ProjectLink>('POST', `/wiki/links/apply?source=${encodeURIComponent(source)}&target=${encodeURIComponent(target)}&reverse=${reverse}`),
  dropLink: ({ source, target }: { source: string; target: string }) =>
    send<void>('DELETE', `/wiki/links?source=${encodeURIComponent(source)}&target=${encodeURIComponent(target)}`),
  entries: () => get<ProjectEntry[]>('/wiki/entries'),
  writeEntry: (payload: Omit<ProjectEntry, 'id' | 'updated_at'>) => send<ProjectEntry>('PUT', '/wiki/entries', payload),
  rebuild: (payload: { device?: string; project?: string; limit?: number; offset?: number }) =>
    send<string[]>('POST', `/raw/rebuild?${new URLSearchParams(Object.entries(payload).map(([k, v]) => [k, String(v)])).toString()}`),
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
  login_failure_window_minutes: number
  login_max_failures_per_ip: number
  login_max_failures_per_username: number
  overview_min_new_logs: number
  overview_max_age_days: number
  background_sweep_hours: number
  background_batch_size: number
  display_timezone: string
  verify_max_claims: number
  rebuild_batch_size: number
  rebuild_max_age_days: number
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
  max_concurrency: number
  background_jobs: boolean
  connect_timeout_seconds: number
  timeout_seconds: number
  enabled: boolean
  has_api_key: boolean
  updated_at: string
}
