import { Alert, Box, Button, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material'
import { type FormEvent, useState } from 'react'

import type { APIKey } from '../api'
import { useAPIKeys, useCreateAPIKey, useDeleteAPIKey } from '../hooks'

const noop = () => undefined

const when = (value: string | null, fallback = '—') => (value ? new Date(value).toLocaleString() : fallback)

const IssuedKey = ({ value }: { value: string }) => {
  const [copied, setCopied] = useState(false)
  const copy = () => navigator.clipboard.writeText(value).then(() => setCopied(true), noop)

  return (
    <Alert severity="success" sx={{ mt: 2, '& .MuiAlert-message': { width: '100%' } }}>
      Copy this key now — it is not shown again. Send it as the <code>X-API-Key</code> header.
      <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
        <TextField value={value} size="small" fullWidth onFocus={(event) => event.target.select()} slotProps={{ htmlInput: { readOnly: true } }} />
        {window.isSecureContext && <Button onClick={copy}>{copied ? 'Copied' : 'Copy'}</Button>}
      </Stack>
    </Alert>
  )
}

const CreateForm = () => {
  const create = useCreateAPIKey()

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = event.currentTarget
    const values = new FormData(form)
    const days = String(values.get('expires_in_days') ?? '')
    create.mutate({ name: String(values.get('name')), expires_in_days: days ? Number(days) : null }, { onSuccess: () => form.reset() })
  }

  return (
    <Paper component="form" onSubmit={submit} sx={{ p: 2, mb: 3, maxWidth: 560 }}>
      <Typography variant="h6" gutterBottom>
        New API key
      </Typography>
      <Stack spacing={1.5}>
        <TextField name="name" label="Name" placeholder="macbook hooks" size="small" required />
        <TextField
          name="expires_in_days"
          label="Expires in (days)"
          type="number"
          size="small"
          helperText="Leave empty for a key that never expires."
          slotProps={{ htmlInput: { min: 1 } }}
        />
      </Stack>
      <Button type="submit" variant="contained" sx={{ mt: 2 }} disabled={create.isPending}>
        Create
      </Button>
      {create.error && <Alert severity="error" sx={{ mt: 2 }}>{`Could not create the key: ${create.error.message}`}</Alert>}
      {create.data && <IssuedKey key={create.data.id} value={create.data.key} />}
    </Paper>
  )
}

const KeyTable = () => {
  const { data } = useAPIKeys()
  const remove = useDeleteAPIKey()

  const confirmDelete = (apiKey: APIKey) => {
    if (window.confirm(`Delete “${apiKey.name}”? Anything still using it stops working.`)) remove.mutate(apiKey.id)
  }

  if (!data.length) return <Typography variant="body2">No API keys yet.</Typography>

  return (
    <>
      {remove.error && <Alert severity="error" sx={{ mb: 2 }}>{`Could not delete the key: ${remove.error.message}`}</Alert>}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Name</TableCell>
            <TableCell>Key</TableCell>
            <TableCell>Created</TableCell>
            <TableCell>Last used</TableCell>
            <TableCell>Expires</TableCell>
            <TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {data.map((apiKey) => (
            <TableRow key={apiKey.id}>
              <TableCell>{apiKey.name}</TableCell>
              <TableCell sx={{ fontFamily: 'monospace' }}>{`${apiKey.prefix}…`}</TableCell>
              <TableCell>{when(apiKey.created_at)}</TableCell>
              <TableCell>{when(apiKey.last_used_at, 'never')}</TableCell>
              <TableCell>{when(apiKey.deleted_at, 'never')}</TableCell>
              <TableCell align="right">
                <Button color="error" size="small" onClick={() => confirmDelete(apiKey)} disabled={remove.isPending}>
                  Delete
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </>
  )
}

const APIKeys = () => (
  <Box>
    <CreateForm />
    <Typography variant="h6" gutterBottom>
      API keys
    </Typography>
    <Typography variant="body2" sx={{ mb: 2 }}>
      Hooks and the importer authenticate with these instead of signing in. Only a signed-in browser can manage them.
    </Typography>
    <KeyTable />
  </Box>
)

export default APIKeys
