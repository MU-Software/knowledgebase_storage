import { useMutation, useQueryClient, useSuspenseQuery } from '@tanstack/react-query'

import { api } from './api'

export const useMe = () => useSuspenseQuery({ queryKey: ['me'], queryFn: api.me })

export const useLogin = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.login,
    onSuccess: (user) => {
      client.removeQueries({ predicate: (query) => query.queryKey[0] !== 'me' })
      client.setQueryData(['me'], user)
    },
  })
}

export const useLogout = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.logout,
    onSuccess: () => client.setQueryData(['me'], null),
  })
}

export const useAPIKeys = () => useSuspenseQuery({ queryKey: ['api-keys'], queryFn: api.apiKeys })

export const useCreateAPIKey = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.createAPIKey,
    onSuccess: () => client.invalidateQueries({ queryKey: ['api-keys'] }),
  })
}

export const useDeleteAPIKey = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.deleteAPIKey,
    onSuccess: () => client.invalidateQueries({ queryKey: ['api-keys'] }),
  })
}

export const useProjects = () => useSuspenseQuery({ queryKey: ['projects'], queryFn: api.projects })

export const useNotes = (project?: string) => useSuspenseQuery({ queryKey: ['notes', project], queryFn: () => api.notes(project) })

export const useNote = (path: string) => useSuspenseQuery({ queryKey: ['note', path], queryFn: () => api.note(path) })

export const useJobs = () => useSuspenseQuery({ queryKey: ['jobs'], queryFn: api.jobs, refetchInterval: 10_000 })

export const useSearch = (query: string) => useSuspenseQuery({ queryKey: ['search', query], queryFn: () => api.search(query) })

export const useSettings = () => useSuspenseQuery({ queryKey: ['settings'], queryFn: api.settings })

export const useProviders = () => useSuspenseQuery({ queryKey: ['providers'], queryFn: api.providers })

export const useSaveSettings = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.updateSettings,
    onSuccess: () => client.invalidateQueries({ queryKey: ['settings'] }),
  })
}

export const useSaveProvider = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, patch }: { id?: string; patch: Record<string, unknown> }) => (id ? api.updateProvider(id, patch) : api.createProvider(patch)),
    onSuccess: () => client.invalidateQueries({ queryKey: ['providers'] }),
  })
}

export const useTestProvider = () => useMutation({ mutationFn: api.testProvider })

export const useDeleteProvider = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.deleteProvider,
    onSuccess: () => client.invalidateQueries({ queryKey: ['providers'] }),
  })
}
