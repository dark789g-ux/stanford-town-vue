import { defineStore } from 'pinia'
import type { Simulation, SimStatus } from '@/types/sim'
import {
  listSims,
  getSim,
  createSim,
  deleteSim as apiDeleteSim,
  pauseSim,
  resumeSim,
  stopSim,
  listForks,
  importFork,
  type CreateSimBody,
  type ImportForkBody,
  type ImportResult,
  type ForkInfo,
} from '@/api/sims'
import { ApiError } from '@/api/client'

interface SimulationsState {
  sims: Simulation[]
  loading: boolean
  error: string | null
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message
  if (e instanceof Error) return e.message
  return String(e)
}

export const useSimulationsStore = defineStore('simulations', {
  state: (): SimulationsState => ({
    sims: [],
    loading: false,
    error: null,
  }),

  getters: {
    running: (state): Simulation[] => state.sims.filter((s) => s.status === 'running'),
    byId: (state) => (id: number): Simulation | undefined =>
      state.sims.find((s) => s.id === id),
  },

  actions: {
    /** Upsert a single sim into the list, preserving order. */
    _upsert(sim: Simulation) {
      const idx = this.sims.findIndex((s) => s.id === sim.id)
      if (idx === -1) this.sims.push(sim)
      else this.sims[idx] = sim
    },

    async fetchAll(statusFilter?: SimStatus): Promise<void> {
      this.loading = true
      this.error = null
      try {
        this.sims = await listSims(statusFilter)
      } catch (e) {
        this.error = errMessage(e)
      } finally {
        this.loading = false
      }
    },

    async fetchOne(id: number): Promise<Simulation | null> {
      this.error = null
      try {
        const sim = await getSim(id)
        this._upsert(sim)
        return sim
      } catch (e) {
        this.error = errMessage(e)
        return null
      }
    },

    async createSim(body: CreateSimBody): Promise<Simulation | null> {
      this.error = null
      try {
        const sim = await createSim(body)
        this._upsert(sim)
        return sim
      } catch (e) {
        this.error = errMessage(e)
        return null
      }
    },

    async deleteSim(id: number): Promise<void> {
      this.error = null
      try {
        await apiDeleteSim(id)
        this.sims = this.sims.filter((s) => s.id !== id)
      } catch (e) {
        this.error = errMessage(e)
      }
    },

    async pause(id: number): Promise<void> {
      this.error = null
      try {
        await pauseSim(id)
        await this.fetchOne(id)
      } catch (e) {
        this.error = errMessage(e)
      }
    },

    async resume(id: number): Promise<void> {
      this.error = null
      try {
        await resumeSim(id)
        await this.fetchOne(id)
      } catch (e) {
        this.error = errMessage(e)
      }
    },

    async stop(id: number): Promise<void> {
      this.error = null
      try {
        await stopSim(id)
        await this.fetchOne(id)
      } catch (e) {
        this.error = errMessage(e)
      }
    },

    async fetchForks(): Promise<ForkInfo[]> {
      this.error = null
      try {
        return await listForks()
      } catch (e) {
        this.error = errMessage(e)
        return []
      }
    },

    async importFork(body: ImportForkBody): Promise<ImportResult | null> {
      this.error = null
      try {
        const result = await importFork(body)
        // Pull the freshly imported sim into the list.
        await this.fetchOne(result.sim_id)
        return result
      } catch (e) {
        this.error = errMessage(e)
        return null
      }
    },
  },
})
