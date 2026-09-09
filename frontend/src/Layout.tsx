import { AppBar, Box, Button, Container, Toolbar, Typography } from '@mui/material'
import { Link, Outlet, useLocation } from 'react-router-dom'

import QueryBoundary from './components/QueryBoundary'

const Layout = () => {
  const { pathname } = useLocation()

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
