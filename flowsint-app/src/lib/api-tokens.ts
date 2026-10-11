import type { ApiToken, ApiTokenCreate, ApiTokenScope } from '@/types/api-token'

export const SCOPES: { value: ApiTokenScope; label: string; description: string }[] = [
  { value: 'read', label: 'Read', description: 'View investigations, sketches and nodes' },
  { value: 'write', label: 'Write', description: 'Add, edit and import nodes and relationships' },
  {
    value: 'enrich',
    label: 'Enrich',
    description: 'Run enrichers and flows (sends node values to external services)'
  }
]

export const EXPIRY_OPTIONS: { value: string; label: string; days: number | null }[] = [
  { value: '30', label: '30 days', days: 30 },
  { value: '90', label: '90 days', days: 90 },
  { value: '365', label: '1 year', days: 365 },
  { value: 'never', label: 'Never', days: null }
]

export const DEFAULT_EXPIRY = '90'
export const MAX_NAME_LENGTH = 100

export type TokenStatus = 'active' | 'expired' | 'revoked'

export function tokenStatus(token: ApiToken, now: Date = new Date()): TokenStatus {
  if (token.revoked_at) return 'revoked'
  if (token.expires_at && new Date(token.expires_at) <= now) return 'expired'
  return 'active'
}

/** Active tokens first (newest first within each group, as the API returns them). */
export function sortTokens(tokens: ApiToken[], now: Date = new Date()): ApiToken[] {
  const rank: Record<TokenStatus, number> = { active: 0, expired: 1, revoked: 2 }
  return tokens
    .map((token, index) => ({ token, index }))
    .sort(
      (a, b) =>
        rank[tokenStatus(a.token, now)] - rank[tokenStatus(b.token, now)] || a.index - b.index
    )
    .map(({ token }) => token)
}

/** Validate the create form; returns the payload or the first problem found. */
export function buildCreatePayload(
  name: string,
  expiry: string,
  scopes: ApiTokenScope[]
): { payload: ApiTokenCreate } | { error: string } {
  const trimmed = name.trim()
  if (!trimmed) return { error: 'Give the token a name' }
  if (trimmed.length > MAX_NAME_LENGTH)
    return { error: `Names are at most ${MAX_NAME_LENGTH} characters` }
  if (scopes.length === 0) return { error: 'Pick at least one permission' }
  const option = EXPIRY_OPTIONS.find((o) => o.value === expiry)
  if (!option) return { error: 'Pick an expiry' }
  const ordered = SCOPES.map((s) => s.value).filter((s) => scopes.includes(s))
  return { payload: { name: trimmed, expires_in_days: option.days, scopes: ordered } }
}

export function toggleScope(scopes: ApiTokenScope[], scope: ApiTokenScope): ApiTokenScope[] {
  return scopes.includes(scope) ? scopes.filter((s) => s !== scope) : [...scopes, scope]
}

export function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric'
  })
}
