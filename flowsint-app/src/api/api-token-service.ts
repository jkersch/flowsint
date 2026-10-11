import { fetchWithAuth } from './api'
import type { ApiToken, ApiTokenCreate, CreatedApiToken } from '@/types/api-token'

export const apiTokenService = {
  list: (): Promise<ApiToken[]> => fetchWithAuth('/api/tokens', { method: 'GET' }),
  create: (data: ApiTokenCreate): Promise<CreatedApiToken> =>
    fetchWithAuth('/api/tokens', { method: 'POST', body: JSON.stringify(data) }),
  revoke: (tokenId: string): Promise<null> =>
    fetchWithAuth(`/api/tokens/${encodeURIComponent(tokenId)}`, { method: 'DELETE' })
}
