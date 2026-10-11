import { describe, it, expect } from 'vitest'
import {
  DEFAULT_EXPIRY,
  EXPIRY_OPTIONS,
  MAX_NAME_LENGTH,
  SCOPES,
  buildCreatePayload,
  formatDate,
  sortTokens,
  toggleScope,
  tokenStatus
} from './api-tokens'
import type { ApiToken } from '@/types/api-token'

const NOW = new Date('2026-10-11T12:00:00Z')

const token = (overrides: Partial<ApiToken> = {}): ApiToken => ({
  id: 't1',
  name: 'laptop',
  prefix: 'fsk_abcdefgh',
  scopes: ['read', 'write', 'enrich'],
  created_at: '2026-10-01T00:00:00Z',
  last_used_at: null,
  expires_at: null,
  revoked_at: null,
  ...overrides
})

describe('tokenStatus', () => {
  it('is active without expiry or revocation', () => {
    expect(tokenStatus(token(), NOW)).toBe('active')
  })

  it('is active until the expiry instant', () => {
    expect(tokenStatus(token({ expires_at: '2026-10-11T12:00:01Z' }), NOW)).toBe('active')
    expect(tokenStatus(token({ expires_at: '2026-10-11T12:00:00Z' }), NOW)).toBe('expired')
  })

  it('revoked wins over expired', () => {
    const t = token({ expires_at: '2026-01-01T00:00:00Z', revoked_at: '2026-02-01T00:00:00Z' })
    expect(tokenStatus(t, NOW)).toBe('revoked')
  })

  it('defaults to the current time', () => {
    expect(tokenStatus(token({ expires_at: '2000-01-01T00:00:00Z' }))).toBe('expired')
  })
})

describe('sortTokens', () => {
  it('puts active first, then expired, then revoked, keeping API order', () => {
    const tokens = [
      token({ id: 'revoked', revoked_at: '2026-10-02T00:00:00Z' }),
      token({ id: 'active-new' }),
      token({ id: 'expired', expires_at: '2026-10-05T00:00:00Z' }),
      token({ id: 'active-old' })
    ]
    expect(sortTokens(tokens, NOW).map((t) => t.id)).toEqual([
      'active-new',
      'active-old',
      'expired',
      'revoked'
    ])
  })

  it('does not mutate its input', () => {
    const tokens = [token({ id: 'a', revoked_at: '2026-10-02T00:00:00Z' }), token({ id: 'b' })]
    sortTokens(tokens, NOW)
    expect(tokens.map((t) => t.id)).toEqual(['a', 'b'])
  })
})

describe('buildCreatePayload', () => {
  it('builds a trimmed payload with ordered scopes and days', () => {
    expect(buildCreatePayload('  CI  ', '30', ['enrich', 'read'])).toEqual({
      payload: { name: 'CI', expires_in_days: 30, scopes: ['read', 'enrich'] }
    })
  })

  it('maps "never" to no expiry', () => {
    const result = buildCreatePayload('x', 'never', ['read'])
    expect(result).toEqual({ payload: { name: 'x', expires_in_days: null, scopes: ['read'] } })
  })

  it.each([
    ['   ', '30', ['read'], 'Give the token a name'],
    ['x'.repeat(MAX_NAME_LENGTH + 1), '30', ['read'], 'at most 100'],
    ['ok', '30', [], 'at least one permission'],
    ['ok', '7', ['read'], 'Pick an expiry']
  ] as const)('rejects %j / %s / %j', (name, expiry, scopes, message) => {
    const result = buildCreatePayload(name, expiry, [...scopes])
    expect('error' in result && result.error).toContain(message)
  })

  it('accepts a name of exactly the maximum length', () => {
    expect('payload' in buildCreatePayload('x'.repeat(MAX_NAME_LENGTH), '30', ['read'])).toBe(true)
  })
})

describe('toggleScope', () => {
  it('adds and removes a scope', () => {
    expect(toggleScope(['read'], 'write')).toEqual(['read', 'write'])
    expect(toggleScope(['read', 'write'], 'read')).toEqual(['write'])
  })
})

describe('formatDate', () => {
  it('formats ISO dates and shows a dash for none', () => {
    expect(formatDate('2026-10-01T12:00:00Z')).toBe('Oct 1, 2026')
    expect(formatDate(null)).toBe('—')
  })
})

describe('options', () => {
  it('match the API: scopes and expiry bounds', () => {
    expect(SCOPES.map((s) => s.value)).toEqual(['read', 'write', 'enrich'])
    for (const option of EXPIRY_OPTIONS) {
      if (option.days !== null) {
        expect(option.days).toBeGreaterThanOrEqual(1)
        expect(option.days).toBeLessThanOrEqual(3650)
      }
    }
    expect(EXPIRY_OPTIONS.some((o) => o.value === DEFAULT_EXPIRY)).toBe(true)
  })
})
