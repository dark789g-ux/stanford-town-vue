import { apiClient } from './client'

export interface LlmLogsOpts {
  persona?: string
  prompt_kind?: string
  step_from?: number
  step_to?: number
  limit?: number
  offset?: number
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function getLlmLogs(simId: number, opts: LlmLogsOpts = {}): Promise<any> {
  const res = await apiClient.get(`/sims/${simId}/llm-logs`, { params: opts })
  return res.data
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function getLlmCall(simId: number, callId: number): Promise<any> {
  const res = await apiClient.get(`/sims/${simId}/llm-logs/${callId}`)
  return res.data
}
