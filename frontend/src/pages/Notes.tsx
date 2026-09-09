import { Typography } from '@mui/material'
import { useParams } from 'react-router-dom'

import NoteList from '../components/NoteList'
import { useNotes } from '../hooks'

const Notes = () => {
  const { project } = useParams()
  const { data } = useNotes(project)

  return (
    <>
      <Typography variant="h5" gutterBottom>
        {project}
      </Typography>
      <NoteList notes={data} />
    </>
  )
}

export default Notes
