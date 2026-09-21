import { Chip, Table, TableBody, TableCell, TableHead, TablePagination, TableRow } from '@mui/material'
import { useState, useTransition } from 'react'

import { useJobs } from '../hooks'

const STATUS_COLOR = {
  pending: 'default',
  claimed: 'info',
  done: 'success',
  failed: 'error',
} as const

const PAGE_SIZES = [25, 50, 100, 200]

const Jobs = () => {
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(50)
  const [, startTransition] = useTransition()
  const { data } = useJobs(page * pageSize, pageSize)

  return (
    <>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Status</TableCell>
            <TableCell>Work</TableCell>
            <TableCell>Project</TableCell>
            <TableCell>Agent</TableCell>
            <TableCell>Device</TableCell>
            <TableCell>Summarizer</TableCell>
            <TableCell>Created</TableCell>
            <TableCell>Last activity</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {data.items.map((job) => (
            <TableRow key={job.id}>
              <TableCell>
                <Chip size="small" color={STATUS_COLOR[job.status]} label={job.status} />
              </TableCell>
              <TableCell>{job.kind.replace('_', ' ')}</TableCell>
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
      <TablePagination
        component="div"
        count={data.total}
        page={page}
        rowsPerPage={pageSize}
        rowsPerPageOptions={PAGE_SIZES}
        onPageChange={(_, next) => startTransition(() => setPage(next))}
        onRowsPerPageChange={(event) =>
          startTransition(() => {
            setPageSize(Number(event.target.value))
            setPage(0)
          })
        }
      />
    </>
  )
}

export default Jobs
