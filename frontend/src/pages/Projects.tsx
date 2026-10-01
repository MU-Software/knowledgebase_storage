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
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import type { ProjectMergeResult, ProjectNode } from '../api'
import { useDeleteProject, useMergeProjects, useProjects, useRequestOverview } from '../hooks'

const UNFILED = '_unfiled'

const describe = (project: ProjectNode) => {
  const merged = project.aliases.length ? ` · merged from ${project.aliases.join(', ')}` : ''
  return `${project.note_count} notes${merged}`
}

const describeMerge = (result: ProjectMergeResult) => {
  const merging = result.merging.length ? `, ${result.merging.join(', ')} left for an LLM to merge` : ''
  const archived = result.archived.length ? `, ${result.archived.length} copies kept under archive/` : ''
  return `Moved ${result.moved} notes into ${result.project}${archived}${merging}.`
}

const UNCONFIRMED =
  'Some sessions here were filed by folder name because their directory has no git remote, so the name may not match the real project. Merge it if it belongs elsewhere.'

const MergeInto = ({ project, projects, onClose }: { project: ProjectNode; projects: ProjectNode[]; onClose: () => void }) => {
  const [target, setTarget] = useState('')
  const merge = useMergeProjects()
  const candidates = projects.filter((other) => other.name !== UNFILED && other.name !== project.name)

  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Merge {project.name} into</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          Its notes move over, the name redirects here from now on, and memories that collide are kept under archive/ while an LLM merges them.
        </DialogContentText>
        <TextField select fullWidth size="small" value={target} label="Project" onChange={(event) => setTarget(event.target.value)}>
          {candidates.map((other) => (
            <MenuItem key={other.name} value={other.name}>
              {other.name}
            </MenuItem>
          ))}
        </TextField>
        {merge.error && <Alert severity="error" sx={{ mt: 2 }}>{`Could not do it: ${merge.error.message}`}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => merge.mutate({ source: project.name, target }, { onSuccess: onClose })}
          disabled={merge.isPending || !target}>
          Merge
        </Button>
      </DialogActions>
    </Dialog>
  )
}

const Projects = () => {
  const { data } = useProjects()
  const [merging, setMerging] = useState<ProjectNode | null>(null)
  const merge = useMergeProjects()
  const remove = useDeleteProject()
  const summarize = useRequestOverview()

  const confirmDelete = (project: ProjectNode) => {
    if (window.confirm(`Delete “${project.name}”? ${project.note_count} notes are removed from disk for good.`)) remove.mutate(project.name)
  }

  return (
    <>
      <Typography variant="body2" sx={{ mb: 2 }}>
        Every project is its own directory. How projects relate to each other lives on the Links page, and overviews name the related projects so a
        session can read them from basic-memory.
      </Typography>
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
      {remove.data && <Alert severity="info" sx={{ mb: 2 }}>{`Deleted ${remove.data.project} and its ${remove.data.deleted_notes} notes.`}</Alert>}
      {remove.error && <Alert severity="error" sx={{ mb: 2 }}>{`Could not delete it: ${remove.error.message}`}</Alert>}
      <List>
        {data.map((project) => (
          <ListItem key={project.name} disablePadding>
            <ListItemButton component={Link} to={`/projects/${encodeURIComponent(project.name)}`}>
              <ListItemText
                primary={
                  <Stack component="span" direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                    <span>{project.name}</span>
                    {project.unconfirmed && (
                      <Tooltip title={UNCONFIRMED}>
                        <Chip size="small" color="warning" label="unconfirmed" />
                      </Tooltip>
                    )}
                  </Stack>
                }
                secondary={describe(project)}
              />
            </ListItemButton>
            {project.name !== UNFILED && (
              <Stack direction="row" spacing={1} sx={{ flexShrink: 0, px: 2 }}>
                {project.overview && (
                  <Button size="small" component={Link} to={`/notes/${encodeURI(project.overview)}`}>
                    Overview
                  </Button>
                )}
                <Button size="small" onClick={() => summarize.mutate(project.name)} disabled={summarize.isPending}>
                  Summarize
                </Button>
                <Button size="small" onClick={() => setMerging(project)}>
                  Merge
                </Button>
                <Button size="small" color="error" onClick={() => confirmDelete(project)} disabled={remove.isPending}>
                  Delete
                </Button>
              </Stack>
            )}
          </ListItem>
        ))}
      </List>
      {merging && <MergeInto project={merging} projects={data} onClose={() => setMerging(null)} />}
    </>
  )
}

export default Projects
