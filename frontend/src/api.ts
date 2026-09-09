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
  claimed_by: string | null
  attempts: number
  last_error: string | null
  summarizer: string | null
  note_path: string | null
  completed_at: string | null
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`)
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
  return (await response.json()) as T
}

export const api = {
  settings: () => get<RuntimeSetting>('/settings'),
  updateSettings: (patch: Partial<RuntimeSetting>) => send<RuntimeSetting>('PATCH', '/settings', patch),
  providers: () => get<LLMProvider[]>('/llm-providers'),
  createProvider: (provider: Partial<LLMProvider> & { api_key?: string }) => send<LLMProvider>('POST', '/llm-providers', provider),
  updateProvider: (id: string, patch: Partial<LLMProvider> & { api_key?: string }) => send<LLMProvider>('PATCH', `/llm-providers/${id}`, patch),
  deleteProvider: (id: string) => send<void>('DELETE', `/llm-providers/${id}`),
  projects: () => get<ProjectSummary[]>('/wiki/projects'),
  notes: (project?: string) => get<NoteSummary[]>(`/wiki/notes${project ? `?project=${encodeURIComponent(project)}` : ''}`),
  note: (path: string) => get<NoteDetail>(`/wiki/notes/${path}`),
  search: (q: string) => get<NoteSummary[]>(`/wiki/search?q=${encodeURIComponent(q)}`),
  jobs: () => get<Job[]>('/jobs'),
}

export type RuntimeSetting = {
  document_language: string
  job_max_attempts: number
  job_batch_size: number
  transcript_retention_hours: number
  stale_claim_hours: number
  maintenance_interval_seconds: number
  worker_poll_interval_seconds: number
  updated_at: string
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

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
  return (response.status === 204 ? undefined : await response.json()) as T
}
