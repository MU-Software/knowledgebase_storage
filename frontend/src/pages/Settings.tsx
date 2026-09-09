import { Box, Button, Checkbox, MenuItem, Paper, Stack, TextField, Typography } from '@mui/material'
import type { FormEvent, ReactNode } from 'react'

import type { LLMProvider, RuntimeSetting } from '../api'
import { useDeleteProvider, useProviders, useSaveProvider, useSaveSettings, useSettings } from '../hooks'

const LABEL_WIDTH = 220
const FORM_WIDTH = 560

type Field<K> = { key: K; label: string; type: string; step?: string }

const RUNTIME_FIELDS: Field<keyof RuntimeSetting>[] = [
  { key: 'document_language', label: 'Note language', type: 'text' },
  { key: 'job_max_attempts', label: 'Max attempts', type: 'number' },
  { key: 'job_batch_size', label: 'Batch size', type: 'number' },
  { key: 'transcript_retention_hours', label: 'Transcript retention (h)', type: 'number' },
  { key: 'stale_claim_hours', label: 'Stale claim (h)', type: 'number' },
  { key: 'maintenance_interval_seconds', label: 'Janitor interval (s)', type: 'number' },
  { key: 'worker_poll_interval_seconds', label: 'Worker poll (s)', type: 'number' },
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

const ProviderForm = ({ provider }: { provider?: LLMProvider }) => {
  const save = useSaveProvider()
  const remove = useDeleteProvider()
  const prefix = provider?.id ?? 'new'

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const values = formValues(event.currentTarget)
    const patch: Record<string, unknown> = { ...values, enabled: values.enabled === 'on' }
    for (const { key, type } of PROVIDER_FIELDS) {
      if (type === 'number' && patch[key] !== undefined) patch[key] = Number(patch[key])
    }
    save.mutate({ id: provider?.id, patch })
  }

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
        <Button type="submit" variant="contained" disabled={save.isPending}>
          {provider ? 'Update' : 'Add'}
        </Button>
        {provider && (
          <Button color="error" onClick={() => remove.mutate(provider.id)} disabled={remove.isPending}>
            Delete
          </Button>
        )}
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
        Tried in priority order. A provider is skipped until the job is older than its minimum age.
      </Typography>
      {data.map((provider) => (
        <ProviderForm key={provider.id} provider={provider} />
      ))}
      <ProviderForm />
    </Box>
  )
}

export default Settings
