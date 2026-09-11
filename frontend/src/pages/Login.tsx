import { Alert, Box, Button, Paper, Stack, TextField, Typography } from '@mui/material'
import type { FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import QueryBoundary from '../components/QueryBoundary'
import { useLogin, useMe } from '../hooks'

const LoginForm = () => {
  const { data } = useMe()
  const login = useLogin()
  const { state } = useLocation()

  if (data) return <Navigate to={(state as { from?: string } | null)?.from ?? '/'} replace />

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const values = new FormData(event.currentTarget)
    login.mutate({ username: String(values.get('username')), password: String(values.get('password')) })
  }

  return (
    <Paper component="form" onSubmit={submit} sx={{ p: 3, width: '100%', maxWidth: 360 }}>
      <Typography variant="h6" gutterBottom>
        Knowledgebase
      </Typography>
      <Stack spacing={2}>
        <TextField name="username" label="Username" autoComplete="username" size="small" required autoFocus />
        <TextField name="password" label="Password" type="password" autoComplete="current-password" size="small" required />
        {login.error && <Alert severity="error">{login.error.message}</Alert>}
        <Button type="submit" variant="contained" disabled={login.isPending}>
          Sign in
        </Button>
      </Stack>
    </Paper>
  )
}

const Login = () => (
  <Box sx={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', px: 2 }}>
    <QueryBoundary>
      <LoginForm />
    </QueryBoundary>
  </Box>
)

export default Login
