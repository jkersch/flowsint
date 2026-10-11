export type ApiTokenScope = 'read' | 'write' | 'enrich'

export interface ApiToken {
  id: string
  name: string
  prefix: string
  scopes: ApiTokenScope[]
  created_at: string
  last_used_at: string | null
  expires_at: string | null
  revoked_at: string | null
}

/** Returned once, at creation: the only time the full token is visible. */
export interface CreatedApiToken extends ApiToken {
  token: string
}

export interface ApiTokenCreate {
  name: string
  expires_in_days: number | null
  scopes: ApiTokenScope[]
}
