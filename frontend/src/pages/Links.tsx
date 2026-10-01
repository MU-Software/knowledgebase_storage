import { Alert, Button, Chip, MenuItem, Paper, Stack, TextField, Typography } from '@mui/material'
import { useState } from 'react'

import type { ProjectLink } from '../api'
import { useApplyLink, useDropLink, useEntries, useLinks, useProjects, useRebuild, useWriteEntry, useWriteLink } from '../hooks'

const KINDS: ProjectLink['kind'][] = ['same', 'part_of', 'related']
const FORM_WIDTH = 900
const REBUILD_PAGE = 50

const reads = (link: ProjectLink) =>
  link.kind === 'same'
    ? `${link.source} and ${link.target} are one project`
    : link.kind === 'part_of'
      ? `${link.source} is part of ${link.target}`
      : `${link.source} and ${link.target} depend on each other`

const Links = () => {
  const { data: links } = useLinks()
  const { data: projects } = useProjects()
  const { data: entries } = useEntries()
  const write = useWriteLink()
  const drop = useDropLink()
  const apply = useApplyLink()
  const writeEntry = useWriteEntry()
  const rebuild = useRebuild()

  const [source, setSource] = useState('')
  const [target, setTarget] = useState('')
  const [said, setSaid] = useState('')
  const [slug, setSlug] = useState('')
  const [description, setDescription] = useState('')
  const [offset, setOffset] = useState(0)

  const described = entries.find((entry) => entry.slug === slug)
  const pick = (name: string) => {
    setSlug(name)
    setDescription(entries.find((entry) => entry.slug === name)?.description ?? '')
  }

  const names = projects.map((project) => project.name).sort((left, right) => left.localeCompare(right))

  return (
    <Stack spacing={3} sx={{ maxWidth: FORM_WIDTH }}>
      <Typography variant="h6">Projects in relation</Typography>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" gutterBottom>
          Say how two projects relate
        </Typography>
        <Alert severity="info" sx={{ mb: 2 }}>
          Write it in your own words. Your sentence is kept, and the next judgement reads it as the strongest evidence.
        </Alert>
        <Stack spacing={2}>
          <Stack direction="row" spacing={1}>
            <TextField select label="Source" value={source} onChange={(event) => setSource(event.target.value)} size="small" sx={{ flex: 1 }}>
              {names.map((name) => (
                <MenuItem key={name} value={name}>
                  {name}
                </MenuItem>
              ))}
            </TextField>
            <TextField select label="Target" value={target} onChange={(event) => setTarget(event.target.value)} size="small" sx={{ flex: 1 }}>
              {names.map((name) => (
                <MenuItem key={name} value={name}>
                  {name}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
          <TextField label="In your words" multiline minRows={2} value={said} onChange={(event) => setSaid(event.target.value)} />
          <Stack direction="row" spacing={1}>
            {KINDS.map((kind) => (
              <Button
                key={kind}
                size="small"
                variant="outlined"
                disabled={!source || !target || source === target || write.isPending}
                onClick={() =>
                  write.mutate({ kind, source, target, stated_by_user: said, description: said, confirmed: true }, { onSuccess: () => setSaid('') })
                }>
                {kind}
              </Button>
            ))}
          </Stack>
          {write.error && <Alert severity="error">{write.error.message}</Alert>}
        </Stack>
      </Paper>

      <Stack spacing={1}>
        {links.map((link) => (
          <Paper key={link.id} variant="outlined" sx={{ p: 1, display: 'flex', gap: 1, alignItems: 'center' }}>
            <Chip size="small" label={link.kind} color={link.confirmed ? 'success' : 'default'} />
            <Stack sx={{ flex: 1 }}>
              <Typography variant="body2">{reads(link)}</Typography>
              <Typography variant="caption" color="text.secondary">
                {link.description || link.stated_by_user}
              </Typography>
            </Stack>
            {!link.confirmed && (
              <Button size="small" variant="outlined" onClick={() => write.mutate({ ...link, confirmed: true })}>
                Confirm
              </Button>
            )}
            {link.kind !== 'related' && (
              <Button
                size="small"
                variant="outlined"
                disabled={apply.isPending}
                onClick={() => apply.mutate({ source: link.source, target: link.target })}>
                {link.kind === 'same' ? 'Merge' : 'File under'}
              </Button>
            )}
            <Button size="small" onClick={() => drop.mutate({ source: link.source, target: link.target })}>
              Remove
            </Button>
          </Paper>
        ))}
        {!links.length && <Typography variant="body2">No links yet.</Typography>}
      </Stack>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" gutterBottom>
          What a project is
        </Typography>
        <Stack spacing={2}>
          <Stack direction="row" spacing={1}>
            <TextField select label="Project" value={slug} onChange={(event) => pick(event.target.value)} size="small" sx={{ flex: 1 }}>
              {names.map((name) => (
                <MenuItem key={name} value={name}>
                  {name}
                </MenuItem>
              ))}
            </TextField>
            <Button
              variant="contained"
              disabled={!slug || writeEntry.isPending}
              onClick={() =>
                writeEntry.mutate({
                  slug,
                  description,
                  sources: described?.sources ?? [],
                  container: described?.container ?? false,
                })
              }>
              Save
            </Button>
          </Stack>
          <TextField label="Description" multiline minRows={2} value={description} onChange={(event) => setDescription(event.target.value)} />
          <Typography variant="caption" color="text.secondary">
            {described?.sources.length
              ? `Working directories kept as they are: ${described.sources.map((source) => source.path).join(', ')}`
              : `${entries.length} project${entries.length === 1 ? '' : 's'} described`}
          </Typography>
        </Stack>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" gutterBottom>
          Rebuild notes from the stored transcripts
        </Typography>
        <Stack direction="row" spacing={1} alignItems="center">
          <Button
            variant="outlined"
            disabled={rebuild.isPending}
            onClick={() => rebuild.mutate({ limit: REBUILD_PAGE, offset }, { onSuccess: () => setOffset(offset + REBUILD_PAGE) })}>
            Queue sessions {offset + 1}–{offset + REBUILD_PAGE}
          </Button>
          <Button size="small" disabled={!offset} onClick={() => setOffset(0)}>
            Start over
          </Button>
          {rebuild.data && <Typography variant="body2">{rebuild.data.length} queued</Typography>}
          {rebuild.error && <Alert severity="error">{rebuild.error.message}</Alert>}
        </Stack>
      </Paper>
    </Stack>
  )
}

export default Links
