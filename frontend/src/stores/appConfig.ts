import { defineStore } from 'pinia'
import { getEffectiveConfig, type EffectiveConfig } from '@/api/meta'
import { ApiError } from '@/api/client'

interface AppConfigState {
  config: EffectiveConfig | null
  loading: boolean
  error: string | null
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message
  if (e instanceof Error) return e.message
  return String(e)
}

export const useAppConfigStore = defineStore('appConfig', {
  state: (): AppConfigState => ({
    config: null,
    loading: false,
    error: null,
  }),

  getters: {
    secretKeyPresent: (state): boolean => state.config?.secret_key_present ?? false,
    llmProfilesCount: (state): number => state.config?.llm_profiles_count ?? 0,
  },

  actions: {
    async fetchConfig(): Promise<void> {
      this.loading = true
      this.error = null
      try {
        this.config = await getEffectiveConfig()
      } catch (e) {
        this.error = errMessage(e)
      } finally {
        this.loading = false
      }
    },
  },
})
