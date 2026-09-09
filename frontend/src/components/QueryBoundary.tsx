import { Alert, Button } from '@mui/material'
import { ErrorBoundary, Suspense } from '@suspensive/react'
import type { PropsWithChildren } from 'react'

import Loading from './Loading'

type Props = PropsWithChildren<{ resetKeys?: unknown[] }>

const QueryBoundary = ({ resetKeys, children }: Props) => (
  <ErrorBoundary
    resetKeys={resetKeys}
    fallback={({ error, reset }) => (
      <Alert severity="error" action={<Button onClick={reset}>Retry</Button>} sx={{ my: 2 }}>
        {error.message}
      </Alert>
    )}>
    <Suspense fallback={<Loading />}>{children}</Suspense>
  </ErrorBoundary>
)

export default QueryBoundary
