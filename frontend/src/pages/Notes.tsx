import { Alert, Button, FormControlLabel, Stack, Switch, Typography } from '@mui/material'
import { useState } from 'react'
import { useParams } from 'react-router-dom'

import NoteList from '../components/NoteList'
import { useForgetNotes, useNotes } from '../hooks'

const Notes = () => {
  const { project } = useParams()
  const { data } = useNotes(project)
  const forget = useForgetNotes()
  const [chosen, setChosen] = useState<string[]>([])
  const [purge, setPurge] = useState(false)

  const choose = (path: string) => setChosen((held) => (held.includes(path) ? held.filter((other) => other !== path) : [...held, path]))

  const remove = () => {
    const kept = purge ? 'and its stored transcript, so nothing can rebuild it' : 'but keeping the stored transcript'
    if (!window.confirm(`Delete ${chosen.length} note(s) ${kept}?`)) return
    forget.mutate({ paths: chosen, verdict: purge ? 'purge' : 'hide', reason: '' }, { onSuccess: () => setChosen([]) })
  }

  return (
    <>
      <Typography variant="h5" gutterBottom>
        {project}
      </Typography>
      <Stack direction="row" spacing={2} sx={{ alignItems: 'center', mb: 1 }}>
        <Button size="small" color="error" variant="outlined" disabled={!chosen.length || forget.isPending} onClick={remove}>
          Delete {chosen.length || ''}
        </Button>
        <FormControlLabel
          control={<Switch size="small" checked={purge} onChange={(event) => setPurge(event.target.checked)} />}
          label="also remove the stored transcript"
        />
      </Stack>
      <Alert severity="info" sx={{ mb: 2 }}>
        Deleting a note deletes every copy of its session, in every project, and the session is remembered so a rebuild does not write it again.
      </Alert>
      {forget.error && <Alert severity="error" sx={{ mb: 2 }}>{`Could not delete: ${forget.error.message}`}</Alert>}
      <NoteList notes={data} chosen={chosen} onChoose={choose} />
    </>
  )
}

export default Notes
