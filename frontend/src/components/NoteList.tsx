import { Checkbox, List, ListItem, ListItemButton, ListItemText } from '@mui/material'
import { Link } from 'react-router-dom'

import type { NoteSummary } from '../api'

type Props = {
  notes: NoteSummary[]
  secondary?: (note: NoteSummary) => string
  chosen?: string[]
  onChoose?: (path: string) => void
}

const NoteList = ({ notes, secondary = (note) => note.path, chosen, onChoose }: Props) => (
  <List>
    {notes.map((note) => (
      <ListItem key={note.path} disablePadding>
        {chosen && onChoose && (
          <Checkbox size="small" checked={chosen.includes(note.path)} onChange={() => onChoose(note.path)} sx={{ flexShrink: 0 }} />
        )}
        <ListItemButton component={Link} to={`/notes/${encodeURI(note.path)}`}>
          <ListItemText primary={note.title} secondary={secondary(note)} />
        </ListItemButton>
      </ListItem>
    ))}
  </List>
)

export default NoteList
