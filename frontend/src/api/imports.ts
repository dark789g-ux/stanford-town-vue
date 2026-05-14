// Import/Export API wrappers.
//
// The import side (listing forks + POST /sims/import) already lives in
// `api/sims.ts` — `listForks` / `importFork`. We re-export them here under
// import/export-flavoured names so the Import/Export view has a single home,
// without defining a second copy of the request. The *export* endpoint
// (POST /sims/{id}/export) has no wrapper anywhere else, so it is added here.
import { apiClient } from './client'
import {
  listForks,
  importFork,
  type ForkInfo,
  type ForkSource,
  type ImportForkBody,
  type ImportResult,
  type OnConflict,
} from './sims'

export type {
  ForkInfo,
  ForkSource,
  ImportForkBody,
  ImportResult,
  OnConflict,
}

/** GET /api/sims/import/forks */
export const listForkCandidates = listForks

/** POST /api/sims/import */
export const importSimulation = importFork

export type ExportLayout = 'compressed' | 'live'

export interface ExportSimBody {
  target_dir: string
  layout: ExportLayout
}

export interface ExportResult {
  sim_id: number
  sim_code: string
  output_path: string
}

/** POST /api/sims/{id}/export */
export async function exportSimulation(
  simId: number,
  body: ExportSimBody,
): Promise<ExportResult> {
  const res = await apiClient.post<ExportResult>(`/sims/${simId}/export`, body)
  return res.data
}
