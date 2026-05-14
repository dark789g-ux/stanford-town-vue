import { apiClient } from './client'

export type LlmProvider = 'openai' | 'deepseek' | 'anthropic'

export interface LlmProfileOut {
  id: number
  name: string
  provider: string
  model: string
  base_url: string | null
  max_tokens: number
  temperature: number
  created_at: string
  // NOTE: api_key is NEVER returned by the backend.
}

export interface LlmProfileCreate {
  name: string
  provider: LlmProvider
  model: string
  api_key: string
  base_url?: string | null
  max_tokens?: number
  temperature?: number
  extra?: Record<string, unknown> | null
}

/** Same fields as Create, all optional. Omit/blank api_key to keep the existing key. */
export interface LlmProfileUpdate {
  name?: string
  provider?: LlmProvider
  model?: string
  api_key?: string
  base_url?: string | null
  max_tokens?: number
  temperature?: number
  extra?: Record<string, unknown> | null
}

export interface LlmProfileTestResult {
  ok: boolean
  elapsed_ms: number
  model: string
  sample_response: string | null
  error: string | null
}

export async function listLlmProfiles(): Promise<LlmProfileOut[]> {
  const res = await apiClient.get<LlmProfileOut[]>('/llm-profiles')
  return res.data
}

export async function createLlmProfile(
  body: LlmProfileCreate,
): Promise<LlmProfileOut> {
  const res = await apiClient.post<LlmProfileOut>('/llm-profiles', body)
  return res.data
}

export async function updateLlmProfile(
  id: number,
  body: LlmProfileUpdate,
): Promise<LlmProfileOut> {
  const res = await apiClient.put<LlmProfileOut>(`/llm-profiles/${id}`, body)
  return res.data
}

export async function deleteLlmProfile(id: number): Promise<void> {
  await apiClient.delete(`/llm-profiles/${id}`)
}

export async function testLlmProfile(
  id: number,
): Promise<LlmProfileTestResult> {
  const res = await apiClient.post<LlmProfileTestResult>(
    `/llm-profiles/${id}/test`,
  )
  return res.data
}
