import { defineStore } from 'pinia'
import { getLlmLogs, getLlmCall, type LlmLogsOpts } from '@/api/llm'
import { ApiError } from '@/api/client'

export interface LlmCallSummary {
  id: number
  persona_name: string | null
  step: number | null
  ts: string
  model: string
  provider: string
  prompt_tokens: number
  completion_tokens: number
  latency_ms: number
  error: string | null
}

export type LlmCallDetail = LlmCallSummary & {
  prompt: string
  response: string
}

export interface LlmLogsListOut {
  items: LlmCallSummary[]
  total: number
  offset: number
  limit: number
}

interface LlmLogsState {
  simId: number | null
  logs: LlmCallSummary[]
  total: number
  offset: number
  limit: number
  loading: boolean
  error: string | null
  persona: string
  model: string
  selectedCall: LlmCallDetail | null
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message
  if (e instanceof Error) return e.message
  return String(e)
}

export const useLlmLogsStore = defineStore('llmLogs', {
  state: (): LlmLogsState => ({
    simId: null,
    logs: [],
    total: 0,
    offset: 0,
    limit: 20,
    loading: false,
    error: null,
    persona: '',
    model: '',
    selectedCall: null,
  }),

  actions: {
    async fetch(simId: number, opts?: LlmLogsOpts): Promise<void> {
      this.simId = simId
      this.loading = true
      this.error = null

      const offset = opts?.offset ?? this.offset
      const limit = opts?.limit ?? this.limit
      const persona =
        opts?.persona ?? (this.persona.trim() === '' ? undefined : this.persona.trim())
      const model = this.model.trim() === '' ? undefined : this.model.trim()

      const params: LlmLogsOpts & { model?: string } = { offset, limit }
      if (persona) params.persona = persona
      if (model) params.model = model

      try {
        const data = (await getLlmLogs(simId, params)) as LlmLogsListOut
        this.logs = data.items
        this.total = data.total
        this.offset = data.offset
        this.limit = data.limit
      } catch (e) {
        this.error = errMessage(e)
      } finally {
        this.loading = false
      }
    },

    async loadCall(simId: number, callId: number): Promise<LlmCallDetail | null> {
      this.error = null
      try {
        const detail = (await getLlmCall(simId, callId)) as LlmCallDetail
        this.selectedCall = detail
        return detail
      } catch (e) {
        this.error = errMessage(e)
        return null
      }
    },

    setFilters(persona: string, model: string): void {
      this.persona = persona
      this.model = model
    },

    reset(): void {
      this.simId = null
      this.logs = []
      this.total = 0
      this.offset = 0
      this.limit = 20
      this.loading = false
      this.error = null
      this.persona = ''
      this.model = ''
      this.selectedCall = null
    },
  },
})
