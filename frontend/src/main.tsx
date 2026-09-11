import { CssBaseline, ThemeProvider, createTheme } from '@mui/material'
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import '@fontsource/roboto/400.css'
import '@fontsource/roboto/500.css'
import App from './App'
import { isUnauthorized } from './api'

const theme = createTheme({ colorSchemes: { dark: true } })

const signOutOn401 = (error: Error) => {
  if (isUnauthorized(error)) queryClient.setQueryData(['me'], null)
}

const queryClient: QueryClient = new QueryClient({
  queryCache: new QueryCache({ onError: signOutOn401 }),
  mutationCache: new MutationCache({ onError: signOutOn401 }),
  defaultOptions: { queries: { staleTime: 30_000, retry: (failures, error) => !isUnauthorized(error) && failures < 3 } },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>
)
