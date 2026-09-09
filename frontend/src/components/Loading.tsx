import { Box, CircularProgress } from '@mui/material'
import { Delay } from '@suspensive/react'

const Loading = () => (
  <Delay ms={200}>
    <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
      <CircularProgress />
    </Box>
  </Delay>
)

export default Loading
