import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('./api', () => ({ fetchWithAuth: vi.fn() }))

import { fetchWithAuth } from './api'
import { apiTokenService } from './api-token-service'

const mocked = vi.mocked(fetchWithAuth)

describe('apiTokenService', () => {
  beforeEach(() => {
    mocked.mockReset()
    mocked.mockResolvedValue('result')
  })

  it('lists tokens', async () => {
    await expect(apiTokenService.list()).resolves.toBe('result')
    expect(mocked).toHaveBeenCalledWith('/api/tokens', { method: 'GET' })
  })

  it('creates a token with a JSON body', async () => {
    const data = { name: 'CI', expires_in_days: 30, scopes: ['read' as const] }
    await apiTokenService.create(data)
    expect(mocked).toHaveBeenCalledWith('/api/tokens', {
      method: 'POST',
      body: JSON.stringify(data)
    })
  })

  it('revokes by id, encoding it into the path', async () => {
    await apiTokenService.revoke('a/b')
    expect(mocked).toHaveBeenCalledWith('/api/tokens/a%2Fb', { method: 'DELETE' })
  })

  it('passes API errors through', async () => {
    mocked.mockRejectedValue(new Error('At most 50 active tokens'))
    await expect(apiTokenService.list()).rejects.toThrow('At most 50 active tokens')
  })
})
