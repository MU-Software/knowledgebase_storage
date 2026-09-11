import { AppBar, Box, Button, Container, Toolbar, Typography } from '@mui/material'
import { Link, Outlet, useLocation } from 'react-router-dom'

import QueryBoundary from './components/QueryBoundary'
import { useLogout, useMe } from './hooks'

const Layout = () => {
  const { pathname } = useLocation()
  const { data: me } = useMe()
  const logout = useLogout()

  return (
    <Box>
      <AppBar position="static">
        <Toolbar>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>
            Knowledgebase
          </Typography>
          <Button color="inherit" component={Link} to="/">
            Projects
          </Button>
          <Button color="inherit" component={Link} to="/search">
            Search
          </Button>
          <Button color="inherit" component={Link} to="/jobs">
            Jobs
          </Button>
          <Button color="inherit" component={Link} to="/settings">
            Settings
          </Button>
          <Button color="inherit" component={Link} to="/api-keys">
            API keys
          </Button>
          <Typography variant="body2" sx={{ ml: 2, mr: 1, opacity: 0.8 }}>
            {me?.username}
          </Typography>
          <Button color="inherit" onClick={() => logout.mutate()} disabled={logout.isPending}>
            Sign out
          </Button>
        </Toolbar>
      </AppBar>
      <Container sx={{ py: 3 }}>
        <QueryBoundary resetKeys={[pathname]}>
          <Outlet />
        </QueryBoundary>
      </Container>
    </Box>
  )
}

export default Layout
