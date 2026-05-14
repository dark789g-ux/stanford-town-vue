import { defineStore } from 'pinia'
import {
  listLlmProfiles,
  createLlmProfile,
  updateLlmProfile,
  deleteLlmProfile,
  testLlmProfile,
  type LlmProfileOut,
  type LlmProfileCreate,
  type LlmProfileUpdate,
  type LlmProfileTestResult,
} from '@/api/llmProfiles'
import { ApiError } from '@/api/client'

interface LlmProfilesState {
  profiles: LlmProfileOut[]
  loading: boolean
  error: string | null
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message
  if (e instanceof Error) return e.message
  return String(e)
}

export const useLlmProfilesStore = defineStore('llmProfiles', {
  state: (): LlmProfilesState => ({
    profiles: [],
    loading: false,
    error: null,
  }),

  getters: {
    hasProfiles: (state): boolean => state.profiles.length > 0,
    byId: (state) => (id: number): LlmProfileOut | undefined =>
      state.profiles.find((p) => p.id === id),
  },

  actions: {
    /** Upsert a single profile into the list, preserving order. */
    _upsert(profile: LlmProfileOut) {
      const idx = this.profiles.findIndex((p) => p.id === profile.id)
      if (idx === -1) this.profiles.push(profile)
      else this.profiles[idx] = profile
    },

    async fetchAll(): Promise<void> {
      this.loading = true
      this.error = null
      try {
        this.profiles = await listLlmProfiles()
      } catch (e) {
        this.error = errMessage(e)
      } finally {
        this.loading = false
      }
    },

    async create(body: LlmProfileCreate): Promise<LlmProfileOut> {
      this.error = null
      try {
        const profile = await createLlmProfile(body)
        this._upsert(profile)
        return profile
      } catch (e) {
        this.error = errMessage(e)
        throw e
      }
    },

    async update(
      id: number,
      body: LlmProfileUpdate,
    ): Promise<LlmProfileOut> {
      this.error = null
      try {
        const profile = await updateLlmProfile(id, body)
        this._upsert(profile)
        return profile
      } catch (e) {
        this.error = errMessage(e)
        throw e
      }
    },

    async remove(id: number): Promise<void> {
      this.error = null
      try {
        await deleteLlmProfile(id)
        this.profiles = this.profiles.filter((p) => p.id !== id)
      } catch (e) {
        this.error = errMessage(e)
        throw e
      }
    },

    async test(id: number): Promise<LlmProfileTestResult> {
      this.error = null
      try {
        return await testLlmProfile(id)
      } catch (e) {
        this.error = errMessage(e)
        throw e
      }
    },
  },
})
