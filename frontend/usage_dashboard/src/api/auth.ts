import { ApiError, apiFetch, setToken } from './client'
import type { TokenResponse } from '../types'

export async function loginAccessToken(
  email: string,
  password: string,
): Promise<TokenResponse> {
  const body = new URLSearchParams({
    username: email,
    password,
  })

  const data = await apiFetch<TokenResponse>('/api/v1/login/access-token', {
    method: 'POST',
    auth: false,
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: body.toString(),
  })

  setToken(data.access_token)
  return data
}

export async function probeAdminAccess(): Promise<boolean> {
  try {
    await apiFetch('/api/v1/admin/observability/summary')
    return true
  } catch (err) {
    if (err instanceof ApiError && (err.status === 400 || err.status === 403)) {
      return false
    }
    throw err
  }
}
