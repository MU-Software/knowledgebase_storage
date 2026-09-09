import { useMutation, useQueryClient, useSuspenseQuery } from '@tanstack/react-query'

import { api } from './api'

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

export const useDeleteProvider = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.deleteProvider,
    onSuccess: () => client.invalidateQueries({ queryKey: ['providers'] }),
  })
}
