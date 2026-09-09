import { Box, Button, TextField, Typography } from '@mui/material'
import type { FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'

import NoteList from '../components/NoteList'
import QueryBoundary from '../components/QueryBoundary'
import { useSearch } from '../hooks'

const Results = ({ query }: { query: string }) => {
  const { data } = useSearch(query)

  if (data.length === 0) {
    return <Typography sx={{ py: 2 }}>No results.</Typography>
  }

  return <NoteList notes={data} secondary={(note) => `${note.project} · ${note.path}`} />
}

const Search = () => {
  const [params, setParams] = useSearchParams()
  const query = params.get('q') ?? ''

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const value = String(new FormData(event.currentTarget).get('q') ?? '').trim()
    setParams(value ? { q: value } : {})
  }

  return (
    <>
      <Box component="form" onSubmit={submit} sx={{ display: 'flex', gap: 1, mb: 2 }}>
        <TextField key={query} name="q" defaultValue={query} fullWidth size="small" label="Search" />
        <Button type="submit" variant="contained">
          Search
        </Button>
      </Box>
      {query && (
        <QueryBoundary resetKeys={[query]}>
          <Results query={query} />
        </QueryBoundary>
      )}
    </>
  )
}

export default Search
