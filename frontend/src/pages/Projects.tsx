import { Chip, List, ListItemButton, ListItemText } from '@mui/material'
import { Link } from 'react-router-dom'

import { useProjects } from '../hooks'

const Projects = () => {
  const { data } = useProjects()

  return (
    <List>
      {data.map((project) => (
        <ListItemButton key={project.name} component={Link} to={`/projects/${encodeURIComponent(project.name)}`}>
          <ListItemText primary={project.name} secondary={`${project.note_count} notes`} />
          {project.unconfirmed && <Chip size="small" color="warning" label="unconfirmed" />}
        </ListItemButton>
      ))}
    </List>
  )
}

export default Projects
