import { Box, Chip, Stack, Typography } from '@mui/material'
import MarkdownIt from 'markdown-it'
import { useParams } from 'react-router-dom'

import { useNote } from '../hooks'

const md = new MarkdownIt({ html: false, linkify: true })

const NoteDetail = () => {
  const path = useParams()['*'] ?? ''
  const { data } = useNote(path)

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
      <Box dangerouslySetInnerHTML={{ __html: md.render(data.content) }} />
    </>
  )
}

export default NoteDetail
