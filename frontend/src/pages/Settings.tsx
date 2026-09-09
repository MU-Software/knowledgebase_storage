import { Box, Button, Checkbox, FormControlLabel, MenuItem, Paper, Stack, TextField, Typography } from '@mui/material'
import type { FormEvent } from 'react'

import type { LLMProvider, RuntimeSetting } from '../api'
import { useDeleteProvider, useProviders, useSaveProvider, useSaveSettings, useSettings } from '../hooks'

const RUNTIME_FIELDS: { key: keyof RuntimeSetting; label: string; type: string }[] = [
  { key: 'document_language', label: 'Note language', type: 'text' },
  { key: 'job_max_attempts', label: 'Max attempts', type: 'number' },
  { key: 'job_batch_size', label: 'Batch size', type: 'number' },
  { key: 'transcript_retention_hours', label: 'Transcript retention (h)', type: 'number' },
  { key: 'stale_claim_hours', label: 'Stale claim (h)', type: 'number' },
  { key: 'maintenance_interval_seconds', label: 'Janitor interval (s)', type: 'number' },
  { key: 'worker_poll_interval_seconds', label: 'Worker poll (s)', type: 'number' },
]

const PROVIDER_FIELDS: { key: keyof LLMProvider | 'api_key'; label: string; type: string }[] = [
  { key: 'name', label: 'Name', type: 'text' },
  { key: 'model', label: 'Model', type: 'text' },
  { key: 'base_url', label: 'Base URL', type: 'text' },
  { key: 'api_key', label: 'API key', type: 'password' },
  { key: 'priority', label: 'Priority', type: 'number' },
  { key: 'min_job_age_seconds', label: 'Min job age (s)', type: 'number' },
  { key: 'context_tokens', label: 'Context tokens', type: 'number' },
]

const formValues = (form: HTMLFormElement) => {
  const entries = [...new FormData(form).entries()].filter(([, value]) => value !== '')
  return Object.fromEntries(entries.map(([key, value]) => [key, value as string]))
}

const RuntimeForm = () => {
  const { data } = useSettings()
  const save = useSaveSettings()

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const values = formValues(event.currentTarget)
    save.mutate(Object.fromEntries(RUNTIME_FIELDS.map(({ key, type }) => [key, type === 'number' ? Number(values[key]) : values[key]])))
  }

  return (
    <Paper component="form" onSubmit={submit} sx={{ p: 2, mb: 3 }}>
      <Typography variant="h6" gutterBottom>
        Runtime settings
      </Typography>
      <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 1 }}>
        {RUNTIME_FIELDS.map(({ key, label, type }) => (
          <TextField key={key} name={key} label={label} type={type} defaultValue={data[key]} size="small" />
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
    <Paper component="form" onSubmit={submit} sx={{ p: 2, mb: 2 }}>
      <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 1, alignItems: 'center' }}>
        <TextField name="kind" label="Kind" select defaultValue={provider?.kind ?? 'llama'} size="small" sx={{ minWidth: 120 }}>
          <MenuItem value="llama">llama</MenuItem>
          <MenuItem value="anthropic">anthropic</MenuItem>
        </TextField>
        {PROVIDER_FIELDS.map(({ key, label, type }) => (
          <TextField
            key={key}
            name={key}
            label={key === 'api_key' && provider?.has_api_key ? `${label} (set)` : label}
            type={type}
            defaultValue={key === 'api_key' ? '' : (provider?.[key as keyof LLMProvider] ?? '')}
            size="small"
          />
        ))}
        <FormControlLabel control={<Checkbox name="enabled" defaultChecked={provider?.enabled ?? true} />} label="Enabled" />
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
