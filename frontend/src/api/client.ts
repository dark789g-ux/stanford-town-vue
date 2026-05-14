import axios, { AxiosError, type AxiosInstance } from 'axios'

export class ApiError extends Error {
  status: number | null
  data: unknown
  constructor(message: string, status: number | null, data: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
  }
}

export const apiClient: AxiosInstance = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
  },
})

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status ?? null
    const data = error.response?.data
    const message =
      (data && typeof data === 'object' && 'detail' in data
        ? String((data as Record<string, unknown>).detail)
        : error.message) || 'API request failed'
    // eslint-disable-next-line no-console
    console.error('[api] error', { status, data, url: error.config?.url })
    throw new ApiError(message, status, data)
  },
)
