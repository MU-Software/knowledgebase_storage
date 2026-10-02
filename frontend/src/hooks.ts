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

const useProjectMutation = <TArgs, TResult>(mutationFn: (args: TArgs) => Promise<TResult>) => {
  const client = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['projects'] })
      client.invalidateQueries({ queryKey: ['notes'] })
      client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export const useMergeProjects = () => useProjectMutation(api.mergeProjects)

export const useDeleteProject = () => useProjectMutation(api.deleteProject)

export const useRequestOverview = () => useProjectMutation(api.requestOverview)

export const useNotes = (project?: string) => useSuspenseQuery({ queryKey: ['notes', project], queryFn: () => api.notes(project) })

export const useForgetNotes = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.forgetNotes,
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['notes'] })
      client.invalidateQueries({ queryKey: ['projects'] })
      client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export const useMoveNote = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.moveNote,
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['notes'] })
      client.invalidateQueries({ queryKey: ['note'] })
      client.invalidateQueries({ queryKey: ['projects'] })
    },
  })
}

export const useNote = (path: string) => useSuspenseQuery({ queryKey: ['note', path], queryFn: () => api.note(path) })

export const useJobs = (offset: number, limit: number) =>
  useSuspenseQuery({ queryKey: ['jobs', offset, limit], queryFn: () => api.jobs({ offset, limit }), refetchInterval: 10_000 })

export const useClaimedJobs = () =>
  useSuspenseQuery({ queryKey: ['jobs', 'claimed'], queryFn: () => api.jobs({ offset: 0, limit: 200, status: 'claimed' }), refetchInterval: 10_000 })

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

export const usePrompts = () => useSuspenseQuery({ queryKey: ['prompts'], queryFn: api.prompts })

const usePromptMutation = <T, R>(fn: (input: T) => Promise<R>) => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => client.invalidateQueries({ queryKey: ['prompts'] }),
  })
}

export const useDraftPrompt = () => usePromptMutation(api.draftPrompt)

export const useAmendPrompt = () => usePromptMutation(api.amendPrompt)

export const useActivatePrompt = () => usePromptMutation(api.activatePrompt)

export const useLinks = () => useSuspenseQuery({ queryKey: ['links'], queryFn: api.links })

const useLinkMutation = <T, R>(fn: (input: T) => Promise<R>) => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => client.invalidateQueries({ queryKey: ['links'] }),
  })
}

export const useWriteLink = () => useLinkMutation(api.writeLink)

export const useApplyLink = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.applyLink,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['links'] })
      void client.invalidateQueries({ queryKey: ['projects'] })
    },
  })
}

export const useDropLink = () => useLinkMutation(api.dropLink)

export const useEntries = () => useSuspenseQuery({ queryKey: ['entries'], queryFn: api.entries })

export const useWriteEntry = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.writeEntry,
    onSuccess: () => client.invalidateQueries({ queryKey: ['entries'] }),
  })
}

export const useRebuild = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.rebuild,
    onSuccess: () => client.invalidateQueries({ queryKey: ['jobs'] }),
  })
}
