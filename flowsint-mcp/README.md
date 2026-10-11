# flowsint-mcp

[Model Context Protocol](https://modelcontextprotocol.io) server for Flowsint. It lets an
MCP client such as Claude Code, Claude Desktop or a claude.ai connector work with your
investigations: search entities in a sketch, explore their neighbours, add findings and
run enrichers.

> **Status:** phases 1–4 of [`FlowsintMcpServer.md`](../FlowsintMcpServer.md) are
> implemented (the Atelligo Hub bridge excepted): all tools, a resource and prompts,
> over **stdio** (local) or **streamable HTTP** (shared server), personal API tokens
> with scopes and a web UI for them, and a Docker image. User docs:
> [`docs/developers/mcp-server.mdx`](../docs/developers/mcp-server.mdx).

## How it works

```
MCP client ──stdio or HTTP──▶ flowsint-mcp ──REST + Bearer token──▶ flowsint-api
```

The server is a thin client of the Flowsint REST API. It never connects to Neo4j or
Postgres directly. Every tool call runs as the user whose token is used, so that user
sees and changes exactly what they could in the web UI (investigation roles owner,
admin, editor and viewer apply unchanged).

## Requirements

- A running Flowsint stack: `make dev` or `docker compose up`. The API is on
  `http://localhost:5001` by default.
- [uv](https://docs.astral.sh/uv/) and Python 3.12+ to run it from the repo, or Docker
  for the shared HTTP server.
- A Flowsint user account with access to the investigations you want to use.

## Installation

The package is a member of the repo's uv workspace:

```bash
# from the repo root
uv sync
```

## Configuration

All configuration is done with environment variables.

| Variable | Default | Description |
|---|---|---|
| `FLOWSINT_API_URL` | `http://localhost:5001` | Base URL of the Flowsint API |
| `FLOWSINT_API_TOKEN` | — | stdio only: bearer token for the API (see [Authentication](#authentication)) |
| `FLOWSINT_EMAIL` | — | stdio only: account email, used to log in if no token is set |
| `FLOWSINT_PASSWORD` | — | stdio only: account password, used with `FLOWSINT_EMAIL` |
| `FLOWSINT_MCP_TRANSPORT` | `stdio` | `stdio` (local, one client) or `http` (shared server; the default in the Docker image) |
| `FLOWSINT_MCP_HOST` | `0.0.0.0` | `http` mode: bind address |
| `FLOWSINT_MCP_PORT` | `5002` | `http` mode: port |
| `FLOWSINT_MCP_ALLOWED_HOSTS` | — | `http` mode: comma-separated `Host` values to accept (turns on DNS-rebinding protection) |
| `FLOWSINT_MCP_ALLOWED_ORIGINS` | — | `http` mode: comma-separated `Origin` values to accept |
| `FLOWSINT_MCP_READ_ONLY` | `false` | `true` registers only read-only tools: no writes, no enrichers |
| `FLOWSINT_MCP_ENRICH_TIMEOUT` | `120` | Seconds `run_enricher` / `run_flow` wait for a result by default |

In stdio mode, if both a token and email/password are set, the token is used first;
email and password are then only used to log in again when the token expires. The
server refuses to start without either, and prints which variable is wrong. In `http`
mode these three must **not** be set (see below).

## Authentication

The server needs to act as a Flowsint user. There are three ways to provide credentials.

### 1. Personal API token (recommended)

Create one in the web app under **API tokens** (sidebar), or with your login JWT (see
2. for how to get one), and copy the `fsk_…` value. It is shown only once; Flowsint
stores just a hash of it:

```bash
curl -s -X POST http://localhost:5001/api/tokens \
  -H "Authorization: Bearer <your login JWT>" \
  -H "Content-Type: application/json" \
  -d '{"name": "claude-code laptop", "expires_in_days": 90, "scopes": ["read", "write", "enrich"]}'
```

Scopes limit what a token may do, on top of your investigation roles:

| Scope | Allows | MCP tools that need it |
|---|---|---|
| `read` | Viewing anything | discovery, search, `get_node`, `sketch_summary`, `get_scan` |
| `write` | Adding and changing data | `add_node`, `add_relationship`, `edit_node`, `import_entities` |
| `enrich` | Running enrichers and flows | `run_enricher`, `run_flow` |

A read-only analyst token is `["read"]`; pair it with `FLOWSINT_MCP_READ_ONLY=true` so
the write tools aren't even offered.

Set it as `FLOWSINT_API_TOKEN` (stdio) or send it as the bearer token (HTTP). List your
tokens with `GET /api/tokens` and revoke one with `DELETE /api/tokens/{id}`. Managing
tokens needs a login JWT: an API token can't create or revoke tokens.

### 2. Login JWT

The token the web UI uses:

```bash
curl -s -X POST http://localhost:5001/api/auth/token \
  -d "username=you@example.com" -d "password=…" | jq -r .access_token
```

Set it as `FLOWSINT_API_TOKEN`. **It expires after 60 hours**, after which tool calls fail
with an authentication error and you need a new one.

### 3. Email and password (stdio only)

Set `FLOWSINT_EMAIL` and `FLOWSINT_PASSWORD`. The server logs in at startup and logs in
again by itself when the token expires. This is convenient for local use, but it stores
your password in the MCP client's config file.

In `http` mode the server holds **no credentials at all**. Each client sends its own
token, which the server forwards to the API (see [Shared HTTP server](#shared-http-server)).

## Starting the server

### Locally over stdio

MCP clients start stdio servers themselves. You only register the command. To check that
it starts:

```bash
# from the repo root
FLOWSINT_API_TOKEN=… uv run --package flowsint-mcp flowsint-mcp
# or
FLOWSINT_API_TOKEN=… uv run --package flowsint-mcp python -m flowsint_mcp
```

It then waits for MCP messages on stdin. Stop it with Ctrl+C.

### Shared HTTP server

```bash
FLOWSINT_MCP_TRANSPORT=http uv run --package flowsint-mcp flowsint-mcp
# MCP endpoint: http://0.0.0.0:5002/mcp, health check: /health
```

Or with Docker Compose, next to the API (the service is opt-in via a profile):

```bash
docker compose -f docker-compose.dev.yml --profile mcp up -d mcp    # builds locally
docker compose -f docker-compose.prod.yml --profile mcp up -d mcp   # ghcr.io image
```

The image builds from the repo root: `docker build -f flowsint-mcp/Dockerfile .`.

- Every request to `/mcp` must carry `Authorization: Bearer <token>`; it is forwarded
  to the API, so each person acts with their own rights. Requests without one get 401.
- The server is stateless: run several instances behind a load balancer if needed.
- In prod it binds to `127.0.0.1:5002`. Publish it through your TLS reverse proxy and
  set `FLOWSINT_MCP_ALLOWED_HOSTS` to its public host name.
- `run_enricher` keeps the request open until the enricher finishes (up to its
  timeout): give the proxy a read timeout longer than `FLOWSINT_MCP_ENRICH_TIMEOUT`.

## Connecting a client

### Claude Code

Over stdio, from inside the repo:

```bash
claude mcp add flowsint \
  -e FLOWSINT_API_URL=http://localhost:5001 \
  -e FLOWSINT_API_TOKEN=fsk_… \
  -- uv run --package flowsint-mcp flowsint-mcp
```

To use it from outside the repo, add `--directory /path/to/flowsint` before
`run`. To share the setup with a project, use a `.mcp.json` in that project's root
instead:

```json
{
  "mcpServers": {
    "flowsint": {
      "command": "uv",
      "args": ["--directory", "/path/to/flowsint", "run", "--package", "flowsint-mcp", "flowsint-mcp"],
      "env": {
        "FLOWSINT_API_URL": "http://localhost:5001",
        "FLOWSINT_API_TOKEN": "fsk_…"
      }
    }
  }
}
```

Don't commit a `.mcp.json` that contains a real token.

Over HTTP, against a shared server:

```bash
claude mcp add --transport http flowsint https://flowsint.example.com/mcp \
  --header "Authorization: Bearer fsk_…"
```

Check the connection with `/mcp` inside Claude Code.

### Claude Desktop

Add the same `mcpServers` entry as in `.mcp.json` above to
`claude_desktop_config.json`, then restart Claude Desktop.

### MCP Inspector (testing)

```bash
npx @modelcontextprotocol/inspector \
  -e FLOWSINT_API_TOKEN=… \
  uv run --package flowsint-mcp flowsint-mcp
```

## Available tools

| Group | Tools | Read-only mode |
|---|---|---|
| Discovery | `list_investigations`, `list_sketches`, `get_sketch`, `list_types`, `list_enrichers`, `list_flows` | ✓ |
| Graph read | `search_nodes`, `search_investigation`, `get_node`, `sketch_summary` | ✓ |
| Graph write | `add_node`, `add_relationship`, `edit_node`, `import_entities` | — |
| Actions | `run_enricher`, `run_flow`, `get_scan` | `get_scan` only |

Besides tools, the server offers:

- **Resource** `flowsint://types`: every entity type with its fields and primary
  field, so a client can load it once instead of calling `list_types` repeatedly.
- **Prompts** `investigate_domain(domain, sketch_id)` and
  `investigate_phishing_page(url, sketch_id)`: step-by-step pivot plans that use the
  tools above and end with a summary (hosting, registrar, kit, targets, abuse
  contacts).

Tools that take a node accept either its `ref` (from `search_nodes`) or a type and
label, for example `{"type": "Domain", "label": "evil.example"}`. There are no delete
tools.

`add_node` and `import_entities` go through Flowsint's validated import, so entities
are checked against their type and get the same label the UI would give them; adding
an existing entity updates it instead of duplicating it. `edit_node` can't change the
field that identifies an entity (its primary field).

Enrichers send node values to third-party services (Shodan, WHOIS, …). Clients that
support it will ask for confirmation before running them.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `authentication failed, check token` | Token missing, expired (login JWTs last 60 h) or revoked. Get a new one. |
| HTTP `401` from `/mcp` | The client sent no `Authorization: Bearer` header. |
| HTTP `421 Misdirected Request` | The `Host` header isn't in `FLOWSINT_MCP_ALLOWED_HOSTS`. |
| `no access to this investigation/sketch` | Your user has no role on that investigation. Ask its owner to add you. |
| `This API token lacks the 'write' scope` | The token was created with fewer scopes. Create one with the scopes you need. |
| Client shows the server as failed at startup | Run the start command by hand to see the error. Usually `FLOWSINT_API_URL` is wrong or the API isn't running (`curl $FLOWSINT_API_URL/health`). |
| `run_enricher` returns only a `scan_id` | The enricher took longer than `FLOWSINT_MCP_ENRICH_TIMEOUT`. Check it later with `get_scan`, or raise the timeout. |
| Write tools are missing | `FLOWSINT_MCP_READ_ONLY=true` is set. |

## Development

```bash
cd flowsint-mcp && uv run pytest   # coverage gate (95 %) is part of the config
make test    # all suites, including this one
make lint
make typecheck
```

Tests fake the Flowsint API with `pytest-httpx` and call every tool through a real
in-memory MCP client session. The HTTP app is tested through Starlette's test client,
and two tests start the real process: once over stdio, once over HTTP with the SDK's
streamable-HTTP client.

Every change ships with unit tests. The rules and the required tests per component are
in [`FlowsintMcpServer.md`](../FlowsintMcpServer.md#testing).
