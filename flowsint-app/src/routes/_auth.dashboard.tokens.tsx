import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, KeySquare, Loader2, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { apiTokenService } from '@/api/api-token-service'
import { queryKeys } from '@/api/query-keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from '@/components/ui/table'
import { CopyButton } from '@/components/copy'
import Loader from '@/components/loader'
import ErrorState from '@/components/shared/error-state'
import { PageLayout } from '@/components/layout/page-layout'
import { useConfirm } from '@/components/use-confirm-dialog'
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
} from '@/lib/api-tokens'
import type { ApiToken, ApiTokenScope, CreatedApiToken } from '@/types/api-token'

export const Route = createFileRoute('/_auth/dashboard/tokens')({
  component: ApiTokensPage
})

const STATUS_BADGE = {
  active: 'default',
  expired: 'secondary',
  revoked: 'outline'
} as const

function ApiTokensPage() {
  const [isDialogOpen, setIsDialogOpen] = useState(false)
  const [name, setName] = useState('')
  const [expiry, setExpiry] = useState(DEFAULT_EXPIRY)
  const [scopes, setScopes] = useState<ApiTokenScope[]>(SCOPES.map((s) => s.value))
  const [created, setCreated] = useState<CreatedApiToken | null>(null)
  const queryClient = useQueryClient()
  const { confirm } = useConfirm()

  const {
    data: tokens = [],
    isLoading,
    error,
    refetch
  } = useQuery<ApiToken[]>({
    queryKey: queryKeys.apiTokens.list,
    queryFn: () => apiTokenService.list()
  })

  const createMutation = useMutation({
    mutationFn: apiTokenService.create,
    onSuccess: (token) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.apiTokens.list })
      setCreated(token)
    },
    onError: (err: Error) => toast.error(`Couldn't create the token: ${err.message}`)
  })

  const revokeMutation = useMutation({
    mutationFn: apiTokenService.revoke,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.apiTokens.list })
      toast.success('Token revoked')
    },
    onError: (err: Error) => toast.error(`Couldn't revoke the token: ${err.message}`)
  })

  const resetDialog = () => {
    setName('')
    setExpiry(DEFAULT_EXPIRY)
    setScopes(SCOPES.map((s) => s.value))
    setCreated(null)
  }

  const handleOpenChange = (open: boolean) => {
    setIsDialogOpen(open)
    // The token is shown once; closing the dialog forgets it.
    if (!open) resetDialog()
  }

  const handleCreate = () => {
    const result = buildCreatePayload(name, expiry, scopes)
    if ('error' in result) {
      toast.error(result.error)
      return
    }
    createMutation.mutate(result.payload)
  }

  const handleRevoke = async (token: ApiToken) => {
    const confirmed = await confirm({
      title: 'Revoke API token',
      message: `Revoke "${token.name}" (${token.prefix}…)? Scripts and MCP clients using it stop working immediately. This can't be undone.`
    })
    if (confirmed) revokeMutation.mutate(token.id)
  }

  const now = new Date()

  return (
    <PageLayout
      title="API tokens"
      description="Personal tokens for scripts and the Flowsint MCP server. A token acts as you, with your roles."
      isLoading={isLoading}
      loadingComponent={<Loader />}
      error={error}
      errorComponent={
        <ErrorState
          title="Couldn't load tokens"
          description="Something went wrong while fetching data. Please try again."
          error={error}
          onRetry={() => refetch()}
        />
      }
      actions={
        <Dialog open={isDialogOpen} onOpenChange={handleOpenChange}>
          <DialogTrigger asChild>
            <Button size="sm">
              <Plus className="w-4 h-4 mr-2" />
              New token
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-[480px]">
            {created ? (
              <>
                <DialogHeader>
                  <DialogTitle>Copy your new token</DialogTitle>
                  <DialogDescription>
                    This is the only time it is shown. Store it somewhere safe; if you lose it,
                    revoke it and create a new one.
                  </DialogDescription>
                </DialogHeader>
                <div className="flex items-center gap-2 py-4">
                  <Input readOnly value={created.token} className="font-mono text-xs" />
                  <CopyButton content={created.token} />
                </div>
                <DialogFooter>
                  <Button onClick={() => handleOpenChange(false)}>Done</Button>
                </DialogFooter>
              </>
            ) : (
              <>
                <DialogHeader>
                  <DialogTitle>New API token</DialogTitle>
                  <DialogDescription>
                    Give it only the permissions it needs, and an expiry.
                  </DialogDescription>
                </DialogHeader>
                <div className="grid gap-4 py-4">
                  <div className="grid gap-2">
                    <Label htmlFor="tokenName">Name</Label>
                    <Input
                      id="tokenName"
                      placeholder="e.g. Claude Code laptop, ingest script"
                      maxLength={MAX_NAME_LENGTH}
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="tokenExpiry">Expires</Label>
                    <Select value={expiry} onValueChange={setExpiry}>
                      <SelectTrigger id="tokenExpiry">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {EXPIRY_OPTIONS.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="grid gap-3">
                    <Label>Permissions</Label>
                    {SCOPES.map((scope) => (
                      <div key={scope.value} className="flex items-start gap-3">
                        <Checkbox
                          id={`scope-${scope.value}`}
                          checked={scopes.includes(scope.value)}
                          onCheckedChange={() => setScopes((s) => toggleScope(s, scope.value))}
                        />
                        <div className="grid gap-0.5 leading-none">
                          <Label htmlFor={`scope-${scope.value}`}>{scope.label}</Label>
                          <span className="text-xs text-muted-foreground">{scope.description}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
                <DialogFooter>
                  <Button
                    variant="outline"
                    onClick={() => handleOpenChange(false)}
                    disabled={createMutation.isPending}
                  >
                    Cancel
                  </Button>
                  <Button
                    onClick={handleCreate}
                    disabled={createMutation.isPending || !name.trim() || scopes.length === 0}
                  >
                    {createMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                    Create token
                  </Button>
                </DialogFooter>
              </>
            )}
          </DialogContent>
        </Dialog>
      }
    >
      <div className="w-full">
        {tokens.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 gap-5">
            <KeySquare className="w-12 h-12 text-muted-foreground/40" strokeWidth={1.5} />
            <div className="text-center space-y-2">
              <h3 className="text-xl font-bold text-foreground">No API tokens yet</h3>
              <p className="text-muted-foreground max-w-sm leading-relaxed">
                Create one to connect the Flowsint MCP server or your own scripts.
              </p>
            </div>
            <Button onClick={() => setIsDialogOpen(true)}>
              <Plus className="w-4 h-4 mr-2" />
              Create your first token
            </Button>
          </div>
        ) : (
          <Card className="overflow-hidden">
            <CardHeader className="border-b">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base font-semibold">Tokens</CardTitle>
                <span className="text-xs text-muted-foreground">
                  {tokens.length} {tokens.length === 1 ? 'token' : 'tokens'}
                </span>
              </div>
              <CardDescription className="flex items-center gap-1.5">
                <AlertTriangle className="w-3.5 h-3.5" />
                Anyone with a token can act as you within its permissions.
              </CardDescription>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow className="border-b bg-muted/20">
                    <TableHead className="py-3 px-6">Name</TableHead>
                    <TableHead className="py-3">Permissions</TableHead>
                    <TableHead className="py-3">Created</TableHead>
                    <TableHead className="py-3">Last used</TableHead>
                    <TableHead className="py-3">Expires</TableHead>
                    <TableHead className="py-3">Status</TableHead>
                    <TableHead className="py-3 px-6 text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortTokens(tokens, now).map((token) => {
                    const status = tokenStatus(token, now)
                    return (
                      <TableRow
                        key={token.id}
                        className="hover:bg-muted/30 border-b border-border/50"
                      >
                        <TableCell className="py-4 px-6">
                          <div className="font-medium">{token.name}</div>
                          <div className="font-mono text-xs text-muted-foreground">
                            {token.prefix}…
                          </div>
                        </TableCell>
                        <TableCell className="py-4">
                          <div className="flex flex-wrap gap-1">
                            {token.scopes.map((scope) => (
                              <Badge key={scope} variant="secondary">
                                {scope}
                              </Badge>
                            ))}
                          </div>
                        </TableCell>
                        <TableCell className="py-4 text-sm text-muted-foreground">
                          {formatDate(token.created_at)}
                        </TableCell>
                        <TableCell className="py-4 text-sm text-muted-foreground">
                          {token.last_used_at ? formatDate(token.last_used_at) : 'Never'}
                        </TableCell>
                        <TableCell className="py-4 text-sm text-muted-foreground">
                          {token.expires_at ? formatDate(token.expires_at) : 'Never'}
                        </TableCell>
                        <TableCell className="py-4">
                          <Badge variant={STATUS_BADGE[status]}>{status}</Badge>
                        </TableCell>
                        <TableCell className="text-right py-4 px-6">
                          {status !== 'revoked' && (
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={`Revoke ${token.name}`}
                              className="text-destructive hover:text-destructive hover:bg-destructive/10"
                              onClick={() => handleRevoke(token)}
                              disabled={revokeMutation.isPending}
                            >
                              <Trash2 className="w-4 h-4" />
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}
      </div>
    </PageLayout>
  )
}

export default ApiTokensPage
