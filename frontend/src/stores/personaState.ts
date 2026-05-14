import { defineStore } from 'pinia'
import { getPersonaState, getPersonaMemory } from '@/api/sims'
import { ApiError } from '@/api/client'

export interface PersonaSummary {
  id: number
  name: string
  age: number | null
  plan_text: string | null
}

export interface MemoryNode {
  id: number
  node_id: string
  node_type: string
  created: number
  expiration_step: number | null
  subject: string
  predicate: string
  object: string
  description: string
  poignancy: number
  keywords: string[]
}

export interface PersonaStateOut {
  persona: PersonaSummary
  scratch: Record<string, unknown> | null
  spatial_memory: Record<string, unknown> | null
  recent_memory: MemoryNode[]
}

export interface MemoryListOut {
  items: MemoryNode[]
  total: number
}

export interface MemoryFilterOpts {
  type?: string
  before_step?: number
  limit?: number
  offset?: number
}

interface PersonaStateState {
  simId: number | null
  personaName: string | null
  persona: PersonaSummary | null
  scratch: Record<string, unknown> | null
  spatialMemory: Record<string, unknown> | null
  recentMemory: MemoryNode[]
  memory: MemoryNode[]
  memoryTotal: number
  type: string | null
  offset: number
  limit: number
  loading: boolean
  error: string | null
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message
  if (e instanceof Error) return e.message
  return String(e)
}

export const usePersonaStateStore = defineStore('personaState', {
  state: (): PersonaStateState => ({
    simId: null,
    personaName: null,
    persona: null,
    scratch: null,
    spatialMemory: null,
    recentMemory: [],
    memory: [],
    memoryTotal: 0,
    type: null,
    offset: 0,
    limit: 20,
    loading: false,
    error: null,
  }),

  actions: {
    async load(simId: number, name: string, step?: number): Promise<void> {
      this.simId = simId
      this.personaName = name
      this.loading = true
      this.error = null
      try {
        const data = (await getPersonaState(
          simId,
          name,
          step,
        )) as unknown as PersonaStateOut
        this.persona = data.persona
        this.scratch = data.scratch
        this.spatialMemory = data.spatial_memory
        this.recentMemory = data.recent_memory ?? []
      } catch (e) {
        this.error = errMessage(e)
      } finally {
        this.loading = false
      }
    },

    async loadMemory(
      simId: number,
      name: string,
      opts: MemoryFilterOpts = {},
    ): Promise<void> {
      this.simId = simId
      this.personaName = name
      if (opts.type !== undefined) this.type = opts.type || null
      if (opts.offset !== undefined) this.offset = opts.offset
      if (opts.limit !== undefined) this.limit = opts.limit
      this.loading = true
      this.error = null
      try {
        const data = (await getPersonaMemory(simId, name, {
          type: this.type ?? undefined,
          before_step: opts.before_step,
          offset: this.offset,
          limit: this.limit,
        })) as unknown as MemoryListOut
        this.memory = data.items ?? []
        this.memoryTotal = data.total ?? 0
      } catch (e) {
        this.error = errMessage(e)
      } finally {
        this.loading = false
      }
    },

    reset(): void {
      this.simId = null
      this.personaName = null
      this.persona = null
      this.scratch = null
      this.spatialMemory = null
      this.recentMemory = []
      this.memory = []
      this.memoryTotal = 0
      this.type = null
      this.offset = 0
      this.limit = 20
      this.loading = false
      this.error = null
    },
  },
})
