// Meta + effective-config API wrappers.
//   GET /api/config/effective -> EffectiveConfig
//   GET /api/meta/personas    -> PersonaMetaItem[]
//   GET /api/meta/maps        -> MapMetaItem[]
import { apiClient } from './client'

export interface EffectiveConfig {
  database_url: string
  assets_dir: string
  logs_dir: string
  frontend_dev_origin: string
  secret_key_present: boolean
  llm_profiles_count: number
}

export interface PersonaMetaItem {
  name: string
  age: number
  has_sprite: boolean
  bootstrap_set: string
}

export interface MapMetaItem {
  name: string
  visuals_url: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  meta: Record<string, any>
  sprite_sheet_url: string
}

export interface HealthOut {
  status: string
  version: string
}

export async function getHealth(): Promise<HealthOut> {
  const res = await apiClient.get<HealthOut>('/health')
  return res.data
}

export async function getEffectiveConfig(): Promise<EffectiveConfig> {
  const res = await apiClient.get<EffectiveConfig>('/config/effective')
  return res.data
}

export async function getMetaPersonas(): Promise<PersonaMetaItem[]> {
  const res = await apiClient.get<PersonaMetaItem[]>('/meta/personas')
  return res.data
}

export async function getMetaMaps(): Promise<MapMetaItem[]> {
  const res = await apiClient.get<MapMetaItem[]>('/meta/maps')
  return res.data
}
