import { apiClient } from './client'
import type { Simulation, SimStatus } from '@/types/sim'

// Backend wire shapes. Precise OpenAPI-generated types arrive in M3's codegen;
// these hand-written shapes match the M3a/M3b endpoints documented for M4.

export interface StepMovementOut {
  step: number
  persona_name: string
  x: number
  y: number
  description: string
  pronunciatio: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  chat: any
  location_path: string[] | string | null
}

export interface StepsPage {
  items: StepMovementOut[]
  total: number
  from_step: number
  to_step: number
}

export interface StepDetail {
  step: number
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  environment: any
  movements: StepMovementOut[]
}

export interface CreateSimBody {
  sim_code: string
  fork_sim_code?: string
  personas?: string[]
  inner_voice?: string
  idea?: string
  n_round?: number
  start_hms?: string
  sec_per_step?: number
  maze_name?: string
  llm_profile_id?: number
  start?: boolean
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [k: string]: any
}

export type OnConflict = 'fail' | 'replace' | 'skip'

export interface ImportForkBody {
  source_path: string
  sim_code_override?: string
  on_conflict: OnConflict
}

export interface ImportResult {
  sim_id: number
  sim_code: string
  counts: Record<string, number>
}

export type ForkSource = 'compressed_storage' | 'storage' | 'db'

export interface ForkInfo {
  sim_code: string
  source: ForkSource
  path: string
  persona_names: string[]
  step_count: number
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type PersonaOut = Record<string, any>
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type PersonaState = Record<string, any>

export async function listSims(
  statusFilter?: SimStatus,
  includeDeleted = false,
): Promise<Simulation[]> {
  const params: Record<string, unknown> = { include_deleted: includeDeleted }
  if (statusFilter) params.status = statusFilter
  const res = await apiClient.get<Simulation[]>('/sims', { params })
  return res.data
}

export async function getSim(id: number): Promise<Simulation> {
  const res = await apiClient.get<Simulation>(`/sims/${id}`)
  return res.data
}

export async function createSim(body: CreateSimBody): Promise<Simulation> {
  const res = await apiClient.post<Simulation>('/sims', body)
  return res.data
}

export async function pauseSim(id: number): Promise<void> {
  await apiClient.post(`/sims/${id}/pause`)
}

export async function resumeSim(id: number): Promise<void> {
  await apiClient.post(`/sims/${id}/resume`)
}

export async function stopSim(id: number): Promise<void> {
  await apiClient.post(`/sims/${id}/stop`)
}

export async function deleteSim(id: number): Promise<void> {
  await apiClient.delete(`/sims/${id}`)
}

export async function getSteps(id: number, from?: number, to?: number): Promise<StepsPage> {
  const params: Record<string, number> = {}
  if (from != null) params.from = from
  if (to != null) params.to = to
  const res = await apiClient.get<StepsPage>(`/sims/${id}/steps`, { params })
  return res.data
}

export async function getStep(id: number, step: number): Promise<StepDetail> {
  const res = await apiClient.get<StepDetail>(`/sims/${id}/steps/${step}`)
  return res.data
}

export async function getSimPersonas(id: number): Promise<PersonaOut[]> {
  const res = await apiClient.get<PersonaOut[]>(`/sims/${id}/personas`)
  return res.data
}

export async function getPersonaState(
  id: number,
  name: string,
  step?: number,
  k?: number,
): Promise<PersonaState> {
  const params: Record<string, number> = {}
  if (step != null) params.step = step
  if (k != null) params.k = k
  const res = await apiClient.get<PersonaState>(
    `/sims/${id}/personas/${encodeURIComponent(name)}/state`,
    { params },
  )
  return res.data
}

export interface PersonaMemoryOpts {
  type?: string
  before_step?: number
  limit?: number
  offset?: number
}

export async function getPersonaMemory(
  id: number,
  name: string,
  opts: PersonaMemoryOpts = {},
): Promise<PersonaState> {
  const params: Record<string, string | number> = {}
  if (opts.type != null) params.type = opts.type
  if (opts.before_step != null) params.before_step = opts.before_step
  if (opts.limit != null) params.limit = opts.limit
  if (opts.offset != null) params.offset = opts.offset
  const res = await apiClient.get<PersonaState>(
    `/sims/${id}/personas/${encodeURIComponent(name)}/memory`,
    { params },
  )
  return res.data
}

export async function listForks(): Promise<ForkInfo[]> {
  const res = await apiClient.get<ForkInfo[]>('/sims/import/forks')
  return res.data
}

export async function importFork(body: ImportForkBody): Promise<ImportResult> {
  const res = await apiClient.post<ImportResult>('/sims/import', body)
  return res.data
}
