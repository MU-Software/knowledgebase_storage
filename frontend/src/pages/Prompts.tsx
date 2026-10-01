import { Alert, Box, Button, Checkbox, Chip, MenuItem, Paper, Stack, TextField, Typography } from '@mui/material'
import { useState } from 'react'

import type { Prompt, PromptStage } from '../api'
import { useActivatePrompt, useAmendPrompt, useDraftPrompt, usePrompts } from '../hooks'

const STAGES: PromptStage[] = [
  'session_explore',
  'session_chunk',
  'session_final',
  'session_verify',
  'session_split',
  'project_overview',
  'project_link',
  'memory_merge',
  'note_relations',
]

const BUILT_IN = 'built-in'
const FORM_WIDTH = 900

const Prompts = () => {
  const { data: prompts } = usePrompts()
  const draft = useDraftPrompt()
  const amend = useAmendPrompt()
  const activate = useActivatePrompt()
  const [stage, setStage] = useState<PromptStage>('session_chunk')
  const [editing, setEditing] = useState<Prompt | null>(null)

  const forStage = prompts.filter((prompt) => prompt.stage === stage)
  const active = forStage.find((prompt) => prompt.status === 'active')
  const chosen = editing && editing.stage === stage ? editing : active
  const changed = chosen && active && chosen.id === active.id

  const save = () => {
    if (!chosen) return
    if (chosen.status === 'active') {
      draft.mutate({ ...chosen, label: chosen.label === BUILT_IN ? 'edited' : chosen.label }, { onSuccess: (created) => setEditing(created) })
      return
    }
    amend.mutate({ id: chosen.id, patch: { system: chosen.system, label: chosen.label, thinking: chosen.thinking, max_tokens: chosen.max_tokens } })
  }

  return (
    <Stack spacing={2} sx={{ maxWidth: FORM_WIDTH }}>
      <Typography variant="h6">Prompts</Typography>
      <Alert severity="info">
        Editing an active prompt makes a draft. Activate it when a trial run looks right; the version that was active is archived.
      </Alert>

      <TextField
        select
        label="Stage"
        value={stage}
        onChange={(event) => setStage(event.target.value as PromptStage)}
        size="small"
        sx={{ maxWidth: 320 }}>
        {STAGES.map((name) => (
          <MenuItem key={name} value={name}>
            {name}
          </MenuItem>
        ))}
      </TextField>

      {chosen ? (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Stack spacing={2}>
            <Stack direction="row" spacing={1} alignItems="center">
              <Chip size="small" label={chosen.status} color={chosen.status === 'active' ? 'success' : 'default'} />
              <TextField
                label="Label"
                size="small"
                value={chosen.label}
                onChange={(event) => setEditing({ ...chosen, label: event.target.value })}
                sx={{ maxWidth: 240 }}
              />
              <Box sx={{ flexGrow: 1 }} />
              <Typography variant="body2">thinking</Typography>
              <Checkbox checked={chosen.thinking} onChange={(event) => setEditing({ ...chosen, thinking: event.target.checked })} />
              <TextField
                label="Max tokens"
                size="small"
                type="number"
                value={chosen.max_tokens}
                onChange={(event) => setEditing({ ...chosen, max_tokens: Number(event.target.value) })}
                sx={{ maxWidth: 140 }}
              />
            </Stack>

            <TextField
              label="System prompt"
              multiline
              minRows={16}
              value={chosen.system}
              onChange={(event) => setEditing({ ...chosen, system: event.target.value })}
              helperText="{language} is replaced with the note language from Settings."
            />

            <Stack direction="row" spacing={1}>
              <Button variant="contained" onClick={save} disabled={draft.isPending || amend.isPending}>
                {changed ? 'Save as draft' : 'Save draft'}
              </Button>
              <Button
                variant="outlined"
                onClick={() => activate.mutate(chosen.id, { onSuccess: () => setEditing(null) })}
                disabled={chosen.status === 'active' || activate.isPending}>
                Activate
              </Button>
              <Button onClick={() => setEditing(null)}>Reset</Button>
            </Stack>
          </Stack>
        </Paper>
      ) : null}

      <Typography variant="subtitle2">History</Typography>
      <Stack spacing={1}>
        {forStage.map((prompt) => (
          <Paper key={prompt.id} variant="outlined" sx={{ p: 1, display: 'flex', gap: 1, alignItems: 'center' }}>
            <Chip size="small" label={prompt.status} color={prompt.status === 'active' ? 'success' : 'default'} />
            <Typography variant="body2">{prompt.label || '(no label)'}</Typography>
            <Typography variant="caption" color="text.secondary">
              {new Date(prompt.created_at).toLocaleString()}
            </Typography>
            <Box sx={{ flexGrow: 1 }} />
            <Button size="small" onClick={() => setEditing(prompt)}>
              Open
            </Button>
          </Paper>
        ))}
      </Stack>
    </Stack>
  )
}

export default Prompts
