import { Alert, Box, Button, Checkbox, CircularProgress, MenuItem, Paper, Stack, TextField, Typography } from '@mui/material'
import type { FormEvent, MouseEvent, ReactNode } from 'react'

import type { LLMProvider, ProviderTestResult, RuntimeSetting } from '../api'
import { useDeleteProvider, useProviders, useSaveProvider, useSaveSettings, useSettings, useTestProvider } from '../hooks'

const LABEL_WIDTH = 220
const FORM_WIDTH = 560

type Field<K> = { key: K; label: string; type: string; step?: string }

const RUNTIME_FIELDS: Field<keyof RuntimeSetting>[] = [
  { key: 'document_language', label: 'Note language', type: 'text' },
  { key: 'job_max_attempts', label: 'Max attempts', type: 'number' },
  { key: 'job_batch_size', label: 'Batch size', type: 'number' },
  { key: 'job_idle_seconds', label: 'Summarize after idle (s)', type: 'number' },
  { key: 'transcript_retention_hours', label: 'Transcript retention (h)', type: 'number' },
  { key: 'stale_claim_hours', label: 'Stale claim (h)', type: 'number' },
  { key: 'maintenance_interval_seconds', label: 'Janitor interval (s)', type: 'number' },
  { key: 'worker_poll_interval_seconds', label: 'Worker poll (s)', type: 'number' },
  { key: 'session_ttl_hours', label: 'Login session (h)', type: 'number' },
]

const PROVIDER_FIELDS: Field<keyof LLMProvider | 'api_key'>[] = [
  { key: 'name', label: 'Name', type: 'text' },
  { key: 'model', label: 'Model', type: 'text' },
  { key: 'base_url', label: 'Base URL', type: 'text' },
  { key: 'api_key', label: 'API key', type: 'password' },
  { key: 'priority', label: 'Priority', type: 'number' },
  { key: 'min_job_age_seconds', label: 'Min job age (s)', type: 'number' },
  { key: 'context_tokens', label: 'Context tokens', type: 'number' },
  { key: 'connect_timeout_seconds', label: 'Connect timeout (s)', type: 'number', step: 'any' },
  { key: 'timeout_seconds', label: 'Request timeout (s)', type: 'number', step: 'any' },
]

const noop = () => undefined

const formValues = (form: HTMLFormElement) => {
  const entries = [...new FormData(form).entries()].filter(([, value]) => value !== '')
  return Object.fromEntries(entries.map(([key, value]) => [key, value as string]))
}

const FieldRow = ({ id, label, children }: { id: string; label: string; children: ReactNode }) => (
  <Stack direction="row" spacing={2} sx={{ alignItems: 'center', width: '100%' }}>
    <Typography component="label" htmlFor={id} variant="body2" sx={{ width: LABEL_WIDTH, flexShrink: 0 }}>
      {label}
    </Typography>
    <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
  </Stack>
)

const RuntimeForm = () => {
  const { data } = useSettings()
  const save = useSaveSettings()

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const values = formValues(event.currentTarget)
    save.mutate(Object.fromEntries(RUNTIME_FIELDS.map(({ key, type }) => [key, type === 'number' ? Number(values[key]) : values[key]])))
  }

  return (
    <Paper component="form" onSubmit={submit} sx={{ p: 2, mb: 3, maxWidth: FORM_WIDTH }}>
      <Typography variant="h6" gutterBottom>
        Runtime settings
      </Typography>
      <Stack spacing={1.5}>
        {RUNTIME_FIELDS.map(({ key, label, type }) => (
          <FieldRow key={key} id={`runtime-${key}`} label={label}>
            <TextField id={`runtime-${key}`} name={key} type={type} defaultValue={data[key]} size="small" fullWidth />
          </FieldRow>
        ))}
      </Stack>
      <Button type="submit" variant="contained" sx={{ mt: 2 }} disabled={save.isPending}>
        Save
      </Button>
    </Paper>
  )
}

const took = (ms: number) => (ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`)

const WRAP = { whiteSpace: 'pre-wrap', wordBreak: 'break-word' } as const

const TestOutcome = ({ result }: { result: ProviderTestResult }) => (
  <Alert severity={result.ok ? 'success' : 'error'} sx={WRAP}>
    {result.ok ? `Answered in ${took(result.latency_ms)}, titling the note “${result.detail}”.` : result.detail}
  </Alert>
)

const ProviderForm = ({ provider }: { provider?: LLMProvider }) => {
  const save = useSaveProvider()
  const test = useTestProvider()
  const remove = useDeleteProvider()
  const prefix = provider?.id ?? 'new'

  const persist = (form: HTMLFormElement) => {
    const values = formValues(form)
    const patch: Record<string, unknown> = { ...values, enabled: values.enabled === 'on' }
    for (const { key, type } of PROVIDER_FIELDS) {
      if (type === 'number' && patch[key] !== undefined) patch[key] = Number(patch[key])
    }
    test.reset()
    return save.mutateAsync({ id: provider?.id, patch })
  }

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = event.currentTarget
    // a created provider gets its own form below, so leave this one empty for the next one
    persist(form).then(() => !provider && form.reset(), noop)
  }

  const saveAndTest = (event: MouseEvent<HTMLButtonElement>) => {
    const form = event.currentTarget.form
    if (form) persist(form).then((saved) => test.mutate(saved.id), noop)
  }

  const busy = save.isPending || test.isPending

  return (
    <Paper component="form" onSubmit={submit} sx={{ p: 2, mb: 2, maxWidth: FORM_WIDTH }}>
      <Stack spacing={1.5}>
        <FieldRow id={`${prefix}-kind`} label="Kind">
          <TextField id={`${prefix}-kind`} name="kind" select defaultValue={provider?.kind ?? 'llama'} size="small" fullWidth>
            <MenuItem value="llama">llama</MenuItem>
            <MenuItem value="anthropic">anthropic</MenuItem>
          </TextField>
        </FieldRow>
        {PROVIDER_FIELDS.map(({ key, label, type, step }) => (
          <FieldRow key={key} id={`${prefix}-${key}`} label={key === 'api_key' && provider?.has_api_key ? `${label} (set)` : label}>
            <TextField
              id={`${prefix}-${key}`}
              name={key}
              type={type}
              defaultValue={key === 'api_key' ? '' : (provider?.[key as keyof LLMProvider] ?? '')}
              size="small"
              fullWidth
              slotProps={step ? { htmlInput: { step } } : undefined}
            />
          </FieldRow>
        ))}
        <FieldRow id={`${prefix}-enabled`} label="Enabled">
          <Checkbox id={`${prefix}-enabled`} name="enabled" defaultChecked={provider?.enabled ?? true} />
        </FieldRow>
      </Stack>
      <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
        <Button type="submit" variant="contained" disabled={busy}>
          {provider ? 'Update' : 'Add'}
        </Button>
        <Button onClick={saveAndTest} disabled={busy} startIcon={test.isPending ? <CircularProgress size={16} /> : undefined}>
          {test.isPending ? 'Calling…' : 'Save & test'}
        </Button>
        {provider && (
          <Button color="error" onClick={() => remove.mutate(provider.id)} disabled={remove.isPending}>
            Delete
          </Button>
        )}
      </Stack>
      <Stack spacing={1} sx={{ mt: save.error || test.error || test.data ? 2 : 0 }}>
        {save.error && <Alert severity="error" sx={WRAP}>{`Could not save: ${save.error.message}`}</Alert>}
        {test.error && <Alert severity="error" sx={WRAP}>{`Could not run the test: ${test.error.message}`}</Alert>}
        {test.data && <TestOutcome result={test.data} />}
      </Stack>
    </Paper>
  )
}

const Settings = () => {
  const { data } = useProviders()

  return (
    <Box>
      <RuntimeForm />
      <Typography variant="h6" gutterBottom>
        LLM providers
      </Typography>
      <Typography variant="body2" sx={{ mb: 2 }}>
        Tried in priority order. A provider is skipped until the session has been quiet for its minimum age. “Save & test” summarizes a throwaway transcript
        with the saved provider, so a self-hosted base URL has to be reachable from the API server.
      </Typography>
      {data.map((provider) => (
        <ProviderForm key={provider.id} provider={provider} />
      ))}
      <ProviderForm />
    </Box>
  )
}

export default Settings
