import { apiClient } from './client'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function getEffectiveConfig(): Promise<any> {
  const res = await apiClient.get('/config/effective')
  return res.data
}
