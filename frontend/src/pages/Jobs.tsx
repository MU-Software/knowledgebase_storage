import { Chip, Table, TableBody, TableCell, TableHead, TableRow } from '@mui/material'

import { useJobs } from '../hooks'

const STATUS_COLOR = {
  pending: 'default',
  claimed: 'info',
  done: 'success',
  failed: 'error',
} as const

const Jobs = () => {
  const { data } = useJobs()

  return (
    <Table size="small">
      <TableHead>
        <TableRow>
          <TableCell>Status</TableCell>
          <TableCell>Project</TableCell>
          <TableCell>Agent</TableCell>
          <TableCell>Device</TableCell>
          <TableCell>Summarizer</TableCell>
          <TableCell>Created</TableCell>
          <TableCell>Last activity</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {data.map((job) => (
          <TableRow key={job.id}>
            <TableCell>
              <Chip size="small" color={STATUS_COLOR[job.status]} label={job.status} />
            </TableCell>
            <TableCell>{job.project}</TableCell>
            <TableCell>{job.agent}</TableCell>
            <TableCell>{job.device}</TableCell>
            <TableCell>{job.summarizer ?? '—'}</TableCell>
            <TableCell>{new Date(job.created_at).toLocaleString()}</TableCell>
            <TableCell>{new Date(job.last_activity_at).toLocaleString()}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export default Jobs
