import { List, ListItemButton, ListItemText } from '@mui/material'
import { Link } from 'react-router-dom'

import type { NoteSummary } from '../api'

type Props = {
  notes: NoteSummary[]
  secondary?: (note: NoteSummary) => string
}

const NoteList = ({ notes, secondary = (note) => note.path }: Props) => (
  <List>
    {notes.map((note) => (
      <ListItemButton key={note.path} component={Link} to={`/notes/${encodeURI(note.path)}`}>
        <ListItemText primary={note.title} secondary={secondary(note)} />
      </ListItemButton>
    ))}
  </List>
)

export default NoteList
