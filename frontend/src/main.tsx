import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import App from './App'
import { AuthProvider } from './auth/AuthContext'
import './styles.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Upstream TWSE is rate limited, so don't refetch on every window focus.
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 60_000,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {/* Innermost because it needs both: useQueryClient to drop the
            per-user caches on sign-out, and router context to redirect. */}
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
