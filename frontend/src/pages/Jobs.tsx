import { Chip, Paper, Stack, Table, TableBody, TableCell, TableHead, TablePagination, TableRow, Typography } from '@mui/material'
import { useEffect, useTransition } from 'react'
import { useSearchParams } from 'react-router-dom'

import type { Job } from '../api'
import { useClaimedJobs, useJobs, useProviders } from '../hooks'

const STATUS_COLOR = {
  pending: 'default',
  claimed: 'info',
  done: 'success',
  failed: 'error',
} as const

const PAGE_SIZES = [25, 50, 100, 200]
const DEFAULT_PAGE_SIZE = 50
const PAGE_SIZE_KEY = 'jobs.pageSize'
const SILENT_AFTER_MS = 10 * 60_000

const rememberedPageSize = () => {
  try {
    return Number(localStorage.getItem(PAGE_SIZE_KEY))
  } catch {
    return 0
  }
}

const rememberPageSize = (size: number) => {
  try {
    localStorage.setItem(PAGE_SIZE_KEY, String(size))
  } catch {
    return
  }
}

const providerOf = (job: Job) => (job.claimed_by ? job.claimed_by.slice(job.claimed_by.indexOf(':') + 1) : '')

const elapsed = (from: string, now: number) => {
  const minutes = Math.max(0, Math.floor((now - Date.parse(from)) / 60_000))
  return minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

const Working = () => {
  const { data: providers } = useProviders()
  const { data: claimed, dataUpdatedAt: now } = useClaimedJobs()

  const enabled = providers.filter((provider) => provider.enabled).sort((left, right) => left.priority - right.priority)
  const names = [...new Set([...enabled.map((provider) => provider.name), ...claimed.items.map(providerOf)])]

  return (
    <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
      <Typography variant="subtitle2" gutterBottom>
        Working now
      </Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Provider</TableCell>
            <TableCell>Work</TableCell>
            <TableCell>Project</TableCell>
            <TableCell>Agent</TableCell>
            <TableCell>Device</TableCell>
            <TableCell>Running for</TableCell>
            <TableCell>Last heartbeat</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {names.map((name) => {
            const label = (
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                <span>{name}</span>
                {!enabled.some((provider) => provider.name === name) && <Chip size="small" label="disabled" />}
              </Stack>
            )
            const jobs = claimed.items.filter((job) => providerOf(job) === name)
            if (jobs.length === 0) {
              return (
                <TableRow key={name}>
                  <TableCell>{label}</TableCell>
                  <TableCell colSpan={6} sx={{ color: 'text.secondary' }}>
                    idle
                  </TableCell>
                </TableRow>
              )
            }
            return jobs.map((job) => {
              const claimedAt = job.claimed_at ?? job.last_activity_at
              const beat = job.heartbeat_at && Date.parse(job.heartbeat_at) > Date.parse(claimedAt) ? job.heartbeat_at : null
              const silent = now - Date.parse(beat ?? claimedAt) > SILENT_AFTER_MS
              return (
                <TableRow key={job.id}>
                  <TableCell>{label}</TableCell>
                  <TableCell>{job.kind.replace('_', ' ')}</TableCell>
                  <TableCell>{job.project}</TableCell>
                  <TableCell>{job.agent}</TableCell>
                  <TableCell>{job.device}</TableCell>
                  <TableCell>{elapsed(claimedAt, now)}</TableCell>
                  <TableCell sx={silent ? { color: 'warning.main' } : undefined}>{beat ? `${elapsed(beat, now)} ago` : 'none yet'}</TableCell>
                </TableRow>
              )
            })
          })}
        </TableBody>
      </Table>
    </Paper>
  )
}

const Jobs = () => {
  const [params, setParams] = useSearchParams()
  const [, startTransition] = useTransition()

  const requestedSize = Number(params.get('size'))
  const fallbackSize = rememberedPageSize()
  const pageSize = PAGE_SIZES.includes(requestedSize) ? requestedSize : PAGE_SIZES.includes(fallbackSize) ? fallbackSize : DEFAULT_PAGE_SIZE
  const page = Math.max(0, Math.floor(Number(params.get('page')) || 1) - 1)

  const { data } = useJobs(page * pageSize, pageSize)
  const lastPage = Math.max(0, Math.ceil(data.total / pageSize) - 1)

  const go = (nextPage: number, nextSize: number) =>
    startTransition(() => setParams({ page: String(nextPage + 1), size: String(nextSize) }, { replace: true }))

  useEffect(() => {
    if (page > lastPage) {
      setParams({ page: String(lastPage + 1), size: String(pageSize) }, { replace: true })
    }
  }, [page, lastPage, pageSize, setParams])

  return (
    <>
      <Working />
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
        page={Math.min(page, lastPage)}
        rowsPerPage={pageSize}
        rowsPerPageOptions={PAGE_SIZES}
        labelDisplayedRows={({ from, to, count }) => `${from}–${to} of ${count} · page ${Math.min(page, lastPage) + 1} of ${lastPage + 1}`}
        showFirstButton
        showLastButton
        onPageChange={(_, next) => go(next, pageSize)}
        onRowsPerPageChange={(event) => {
          const size = Number(event.target.value)
          rememberPageSize(size)
          go(0, size)
        }}
      />
    </>
  )
}

export default Jobs
