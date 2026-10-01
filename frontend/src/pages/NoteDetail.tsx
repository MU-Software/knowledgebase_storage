import { Alert, Box, Button, Chip, MenuItem, Paper, Stack, TextField, Typography } from '@mui/material'
import MarkdownIt from 'markdown-it'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { useMoveNote, useNote, useProjects } from '../hooks'

const md = new MarkdownIt({ html: false, linkify: true })

const NoteDetail = () => {
  const path = useParams()['*'] ?? ''
  const { data } = useNote(path)
  const { data: projects } = useProjects()
  const move = useMoveNote()
  const navigate = useNavigate()
  const [target, setTarget] = useState('')
  const [first, setFirst] = useState('')
  const [last, setLast] = useState('')

  const names = projects.map((project) => project.name).filter((name) => name !== data.project)

  const submit = () =>
    move.mutate(
      { path, project: target, first_request: Number(first) || 0, last_request: Number(last) || 0 },
      { onSuccess: (result) => result.moved_to && navigate(`/notes/${encodeURI(result.moved_to)}`, { replace: true }) }
    )

  return (
    <>
      <Typography variant="h4" gutterBottom>
        {data.title}
      </Typography>
      <Stack direction="row" spacing={1} sx={{ mb: 2, flexWrap: 'wrap' }}>
        {data.tags.map((tag) => (
          <Chip key={tag} size="small" label={tag} />
        ))}
      </Stack>

      <Paper variant="outlined" sx={{ p: 2, mb: 3 }}>
        <Typography variant="subtitle2" gutterBottom>
          File this under another project
        </Typography>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
          <TextField select size="small" label="Project" value={target} onChange={(event) => setTarget(event.target.value)} sx={{ minWidth: 220 }}>
            {names.map((name) => (
              <MenuItem key={name} value={name}>
                {name}
              </MenuItem>
            ))}
          </TextField>
          <TextField size="small" label="From request" value={first} onChange={(event) => setFirst(event.target.value)} sx={{ width: 130 }} />
          <TextField size="small" label="To request" value={last} onChange={(event) => setLast(event.target.value)} sx={{ width: 130 }} />
          <Button variant="contained" disabled={!target || move.isPending} onClick={submit}>
            Move
          </Button>
        </Stack>
        <Typography variant="caption" color="text.secondary">
          Leave the request numbers empty to move the whole session. Give a range to send only those requests there from the next rebuild on.
        </Typography>
        {move.error && <Alert severity="error" sx={{ mt: 1 }}>{`Could not move it: ${move.error.message}`}</Alert>}
        {move.isSuccess && !move.data.moved_to && (
          <Alert severity="info" sx={{ mt: 1 }}>
            Pinned. Those requests move to {move.data.project} the next time this session is summarized.
          </Alert>
        )}
      </Paper>

      <Box dangerouslySetInnerHTML={{ __html: md.render(data.content) }} />
    </>
  )
}

export default NoteDetail
