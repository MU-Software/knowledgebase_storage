import {
  Alert,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import type { ProjectMergeResult, ProjectNode, Suggestion } from '../api'
import {
  useDecideSuggestion,
  useDeleteProject,
  useMergeProjects,
  useProjects,
  useReparentProject,
  useRequestOverview,
  useSuggestions,
} from '../hooks'

const UNFILED = '_unfiled'
const TOP_LEVEL = ''

type Action = { kind: 'merge' | 'move'; project: ProjectNode }

const inside = (project: ProjectNode) => (other: ProjectNode) => other.path === project.path || other.path.startsWith(`${project.path}/`)

const describe = (project: ProjectNode) => {
  const notes = `${project.note_count} notes`
  const nested = project.child_count ? `, ${project.total_note_count} with ${project.child_count} inside` : ''
  const merged = project.aliases.length ? ` · merged from ${project.aliases.join(', ')}` : ''
  return `${notes}${nested}${merged}`
}

const describeMerge = (result: ProjectMergeResult) => {
  const merging = result.merging.length ? `, ${result.merging.join(', ')} left for an LLM to merge` : ''
  const archived = result.archived.length ? `, ${result.archived.length} copies kept under archive/` : ''
  return `Moved ${result.moved} notes into ${result.project}${archived}${merging}.`
}

const reads = (suggestion: Suggestion) =>
  suggestion.kind === 'merge' ? `Merge ${suggestion.source} into ${suggestion.target}` : `File ${suggestion.source} under ${suggestion.target}`

const Suggestions = () => {
  const { data } = useSuggestions()
  const decide = useDecideSuggestion()

  if (!data.length) return null

  return (
    <Paper sx={{ p: 2, mb: 3 }}>
      <Typography variant="subtitle1" gutterBottom>
        What the idle providers noticed
      </Typography>
      {decide.error && <Alert severity="error" sx={{ mb: 1 }}>{`Could not act on it: ${decide.error.message}`}</Alert>}
      <Stack spacing={1}>
        {data.map((suggestion) => (
          <Stack key={suggestion.id} direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <Typography variant="body2" sx={{ flex: 1 }}>
              <b>{reads(suggestion)}</b> — {suggestion.reason}
            </Typography>
            <Button size="small" variant="outlined" disabled={decide.isPending} onClick={() => decide.mutate({ id: suggestion.id, applied: true })}>
              Apply
            </Button>
            <Button size="small" disabled={decide.isPending} onClick={() => decide.mutate({ id: suggestion.id, applied: false })}>
              Dismiss
            </Button>
          </Stack>
        ))}
      </Stack>
    </Paper>
  )
}

const MoveOrMerge = ({ action, projects, onClose }: { action: Action; projects: ProjectNode[]; onClose: () => void }) => {
  const [target, setTarget] = useState(TOP_LEVEL)
  const merge = useMergeProjects()
  const move = useReparentProject()
  const mutation = action.kind === 'merge' ? merge : move
  const candidates = projects.filter((project) => project.path !== UNFILED && !inside(action.project)(project))

  const submit = () => {
    if (action.kind === 'merge') merge.mutate({ source: action.project.name, target }, { onSuccess: onClose })
    else move.mutate({ name: action.project.name, parent: target || null }, { onSuccess: onClose })
  }

  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>{action.kind === 'merge' ? `Merge ${action.project.name} into` : `File ${action.project.name} under`}</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          {action.kind === 'merge'
            ? 'Its notes move over, the name redirects here from now on, and memories that collide are kept under archive/ while an LLM merges them.'
            : 'Its notes move under the new parent, and sessions there see this project as background.'}
        </DialogContentText>
        <TextField select fullWidth size="small" value={target} label="Project" onChange={(event) => setTarget(event.target.value)}>
          {action.kind === 'move' && <MenuItem value={TOP_LEVEL}>— top level —</MenuItem>}
          {candidates.map((project) => (
            <MenuItem key={project.path} value={project.name}>
              {project.path}
            </MenuItem>
          ))}
        </TextField>
        {mutation.error && <Alert severity="error" sx={{ mt: 2 }}>{`Could not do it: ${mutation.error.message}`}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={submit} disabled={mutation.isPending || (action.kind === 'merge' && !target)}>
          {action.kind === 'merge' ? 'Merge' : 'Move'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

const Projects = () => {
  const { data } = useProjects()
  const [action, setAction] = useState<Action | null>(null)
  const merge = useMergeProjects()
  const remove = useDeleteProject()
  const summarize = useRequestOverview()

  const confirmDelete = (project: ProjectNode) => {
    const nested = project.child_count ? ` and ${project.child_count} projects inside it` : ''
    if (window.confirm(`Delete “${project.path}”? ${project.total_note_count} notes${nested} are removed from disk for good.`))
      remove.mutate(project.name)
  }

  return (
    <>
      <Typography variant="body2" sx={{ mb: 2 }}>
        A project holds the ones filed under it: their notes show up here, and their sessions start with this one as background. An idle provider
        keeps each project’s overview up to date and works its way up the tree.
      </Typography>
      <Suggestions />
      {merge.data && (
        <Alert severity="success" sx={{ mb: 2 }}>
          {describeMerge(merge.data)}
        </Alert>
      )}
      {summarize.error && <Alert severity="error" sx={{ mb: 2 }}>{`Could not queue the summary: ${summarize.error.message}`}</Alert>}
      {summarize.isSuccess && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {summarize.data ? 'Queued. The next idle provider writes it.' : 'Already queued, or there are no sessions to summarize yet.'}
        </Alert>
      )}
      {remove.data && <Alert severity="info" sx={{ mb: 2 }}>{`Deleted ${remove.data.path} and its ${remove.data.deleted_notes} notes.`}</Alert>}
      {remove.error && <Alert severity="error" sx={{ mb: 2 }}>{`Could not delete it: ${remove.error.message}`}</Alert>}
      <List>
        {data.map((project) => (
          <ListItem
            key={project.path}
            disablePadding
            secondaryAction={
              project.path !== UNFILED && (
                <Stack direction="row" spacing={1}>
                  {project.overview && (
                    <Button size="small" component={Link} to={`/notes/${encodeURI(project.overview)}`}>
                      Overview
                    </Button>
                  )}
                  <Button size="small" onClick={() => summarize.mutate(project.name)} disabled={summarize.isPending}>
                    Summarize
                  </Button>
                  <Button size="small" onClick={() => setAction({ kind: 'move', project })}>
                    Move
                  </Button>
                  <Button size="small" onClick={() => setAction({ kind: 'merge', project })}>
                    Merge
                  </Button>
                  <Button size="small" color="error" onClick={() => confirmDelete(project)} disabled={remove.isPending}>
                    Delete
                  </Button>
                </Stack>
              )
            }>
            <ListItemButton
              component={Link}
              to={`/projects/${encodeURIComponent(project.name)}`}
              sx={{ pl: 2 + project.depth * 3, pr: project.path === UNFILED ? 2 : 44 }}>
              <ListItemText primary={project.name} secondary={describe(project)} />
              {project.unconfirmed && <Chip size="small" color="warning" label="unconfirmed" />}
            </ListItemButton>
          </ListItem>
        ))}
      </List>
      {action && <MoveOrMerge action={action} projects={data} onClose={() => setAction(null)} />}
    </>
  )
}

export default Projects
