// Hand-written types used immediately by the frontend.
// These will be superseded / cross-checked by openapi-typescript output in M3.

export type SimStatus =
  | 'idle'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'interrupted'
  | 'stopped'

export interface Simulation {
  id: number
  sim_code: string
  status: SimStatus
  curr_time_iso: string | null
  step: number
  n_round: number
  sec_per_step: number
  start_time_iso: string | null
  fork_sim_code?: string | null
  maze_name?: string | null
  llm_profile_id?: number | null
  created_at?: string
  updated_at?: string
  notes?: string | null
}

export interface StepMovement {
  step: number
  persona_name: string
  x: number
  y: number
  description: string
  pronunciatio: string
  chat: unknown
  location_path: string[] | string | null
}
