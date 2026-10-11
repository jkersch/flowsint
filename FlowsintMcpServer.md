# Flowsint MCP Server — Implementation Plan

Design document for `flowsint-mcp`, a Model Context Protocol server that lets an LLM
client (Claude Code, Claude Desktop, claude.ai connectors) investigate with Flowsint:
find entities in a sketch, walk the graph, add findings, and run enrichers.

Branch: `feat/mcp-server`

**Testing rule:** every feature and every change in this plan ships with unit tests in
the same commit — see [Testing](#testing). A task without tests is not done.

## Implementation status

| Phase | State |
|---|---|
| 1 — Read-only MCP + search | ✅ Done |
| 2 — Write + actions | ✅ Done |
| 3 — API keys + HTTP transport | ✅ Done |
| 4 — Polish | ✅ Done, except the Hub bridge (item 12) |

Decisions taken for the open questions (revisit any time): stdio first; writes on by
default with `FLOWSINT_MCP_READ_ONLY` to turn them off; no `flowsint_types` dependency
in the MCP package (the API validates); G5 handled in the MCP (404 ⇒ `QUEUED`).

Deviations from the original design, found while implementing:

- **Search returns `GraphNode`s**, the same shape as the neighbours endpoint, instead
  of a custom `{id, type, label, data}` shape; there is no `total_estimate`. Type
  filters use Neo4j 5 label expressions (`MATCH (n:\`domain\`|\`ip\`)`) so the label
  indexes are used; type names and filter keys are validated against strict patterns
  before they reach Cypher.
- **Stats include `top_nodes`** (most connected nodes, via a `COUNT {}` subquery).
- **`add_node` uses the import endpoint, not `/nodes/add`.** `/nodes/add` stores the
  posted `GraphNode` without validating it or computing its label, so a client could
  create duplicates with wrong labels (nodes MERGE on label). To support this,
  `/import/execute` now also returns the written nodes (`nodes: [{ref, id, type,
  label}]`), a backward-compatible addition.
- **Security fix:** `/import/execute` only checked *read* access, so a viewer could
  write nodes. It now requires update rights (`SketchService.get_for_update`), like
  every other write endpoint.
- **G4 covers every type:** migration `005_complete_type_indexes.cypher` adds the
  `sketch_id`/`nodeLabel` indexes missing for 35 types (including `technology`, which
  had none), and a test fails when a new type lacks them.
- `run_enricher`'s completion report gives counts of new nodes by type and new edges;
  it does not list individual new nodes.

Phase 3 decisions:

- **Token management needs a login session.** `/api/tokens` rejects API tokens (403),
  so a leaked token can't create replacements for itself or revoke the owner's others.
- **Deactivated users' tokens stop working** (`is_active` is checked for API tokens; the
  existing JWT path is unchanged). Max 50 active tokens per user; expiry 1–3650 days
  or none; `last_used_at` is written at most once a minute.
- **HTTP mode refuses server-side credentials.** With `FLOWSINT_MCP_TRANSPORT=http`,
  setting `FLOWSINT_API_TOKEN`/`EMAIL`/`PASSWORD` is a startup error, so a shared
  server can never fall back to one user's rights. The caller's bearer token is read
  from the MCP request context and forwarded; `/mcp` without one is a 401 before any
  tool runs.
- **Stateless JSON responses** (`stateless_http`, `json_response`): no session
  affinity, works behind any load balancer. `/health` for container health checks.
- **Compose service is opt-in** (`--profile mcp`) and bound to localhost in prod; a
  `build-mcp` job publishes `ghcr.io/<owner>/flowsint-mcp` with the other images.
- The API image now copies `flowsint-mcp` (a workspace member, needed by
  `uv sync --frozen`) but doesn't install it.
- Token UI in the web app came in phase 4.

Phase 4 decisions:

- **Token scopes** `read`, `write`, `enrich` (default: all). Enforced once, in
  `get_current_user`, from method + path (`flowsint-api/app/api/scopes.py`): reads
  and an explicit list of read-only POSTs need `read`, enricher/flow launches and
  template tests need `enrich`, everything else needs `write`, so a new endpoint is a
  write until classified. A test fails if a registered route's scope changes or a
  classified route disappears. Unknown stored scopes are ignored (deny, never crash).
  Login sessions are not affected by scopes. The `scopes` column is part of the
  still-unreleased `api_tokens` migration.
- **Investigation-wide search** `POST /api/investigations/{id}/nodes/search`: same
  criteria and validation as sketch search (shared `_search_criteria`), needs read
  access to the investigation, searches all its sketches with `n.sketch_id IN [...]`;
  each hit carries `sketch_id` and `sketch_title`. MCP tool `search_investigation`.
- **Resource** `flowsint://types` and **prompts** `investigate_domain`,
  `investigate_phishing_page`. 403 errors now pass the API's reason through (e.g. a
  missing scope).
- **Token UI** at `/dashboard/tokens` (sidebar "API tokens"): list with status,
  create dialog (name, expiry, scopes) that shows the token once, revoke with
  confirmation. Page logic lives in `src/lib/api-tokens.ts` (pure, unit-tested); the
  frontend has no DOM test setup, so the component itself is covered by type checks.
  `routeTree.gen.ts` was extended by hand (the `tsr` CLI version rewrites every
  route file); the Vite plugin regenerates it on the next dev/build and may
  reorder the new entries, which is harmless.
- **Not done: item 12, Hub bridge.** Needs the Atelligo Hub's REST API (base URL,
  auth, submission/phishkit endpoints), which isn't in this repo.

## Why

Flowsint's graph and enrichers are only reachable through the web UI or raw REST calls.
An MCP server turns them into tools an assistant can chain, e.g. *"take the phishing page
from Hub submission 4711, put it into the RBFCU sketch, run DNS + WHOIS + ASN enrichers,
and tell me which other pages share its hosting"*. Combined with the existing
`pike-hub` MCP server this connects case intake (Hub) with infrastructure pivoting
(Flowsint) in one conversation.

## Goals / non-goals

**Goals**

- Read: list investigations/sketches, search nodes, inspect a node and its neighbours,
  summarize a sketch, list types/enrichers/flows.
- Write: add/edit nodes and relationships, bulk-import entities.
- Act: launch an enricher or flow on nodes and report its outcome.
- Enforce the **same permissions as the UI** (investigation roles owner/admin/editor/viewer).
- Keep tool output small enough for an LLM context (paged, compact, never a whole graph).

**Non-goals (v1)**

- Deleting nodes, relationships, sketches or investigations. Irreversible, low value for
  an assistant; can be added later behind `destructiveHint`.
- Managing vault secrets, custom types, collaborators, or flows' definitions.
- Replacing the bulk ingest path (`scripts/bulk_ingest.py`) for 100k-scale loads.
- The in-app chat (`/api/chats`) — separate feature, untouched.

## Architecture decision

```
 MCP client ──stdio / streamable HTTP──▶ flowsint-mcp ──HTTPS + Bearer──▶ flowsint-api ──▶ Postgres / Neo4j / Celery
```

`flowsint-mcp` is a **thin REST client**, not an in-process extension of the API and not a
direct Neo4j client.

| Option | Verdict |
|---|---|
| Talk to Neo4j/Postgres directly | ✗ Bypasses `_check_permission` in the services (e.g. `sketch_service.py:51`) — every tool would have to re-implement ACLs. |
| Mount MCP inside FastAPI (`/mcp`) | Possible later. Couples MCP SDK upgrades to API deploys and still needs per-request auth plumbing. Revisit once the tool set is stable. |
| **Standalone package calling REST** | ✓ Permissions, validation and type resolution stay in one place. Runs locally (stdio) with zero server changes, or as a container (HTTP) next to the API. Same model as `pike-hub`. |

### Package layout

New uv workspace member (add to root `pyproject.toml` `[tool.uv.workspace].members` and
`[tool.uv.sources]`):

```
flowsint-mcp/
  pyproject.toml            # deps: mcp (official Python SDK, FastMCP), httpx, pydantic
  Dockerfile
  README.md
  src/flowsint_mcp/
    __init__.py
    __main__.py             # `python -m flowsint_mcp` / console script `flowsint-mcp`
    server.py               # FastMCP instance, transport selection, tool registration
    config.py               # env settings (see Configuration)
    client.py               # FlowsintClient: httpx.AsyncClient, auth, retries, error mapping
    refs.py                 # NodeRef resolution (type+label -> elementId)
    shaping.py              # compact node/edge rendering, truncation, paging
    tools/
      discovery.py          # investigations, sketches, types, enrichers, flows
      graph_read.py         # search_nodes, get_node, sketch_summary
      graph_write.py        # add_node, add_relationship, edit_node, import_entities
      actions.py            # run_enricher, run_flow, get_scan
  tests/
    conftest.py             # pytest-httpx fixtures, fake API responses
    test_client.py
    test_refs.py
    test_shaping.py
    test_tools_*.py
```

`flowsint-mcp` must **not** import `flowsint_core` (keeps the image small and avoids DB
drivers). It may import `flowsint_types` for client-side validation of `add_node` payloads
and the type list — optional, decide during implementation.

## Gaps in the API (must be closed first)

Verified against current `main` code.

| # | Gap | Today | Needed |
|---|---|---|---|
| G1 | **Node search** | Only `GET /sketches/{id}/graph` (all nodes, `LIMIT 100000`, `repository.py:515`) or lookup by elementId | Filtered, paged search |
| G2 | **Sketch summary** | `count_nodes_by_sketch` / `count_edges_by_sketch` exist in the repository but aren't exposed; no per-type counts | `GET /sketches/{id}/stats` |
| G3 | **Machine auth** | JWT from `POST /api/auth/token`, 60h expiry (`auth.py:18`), no API keys | Personal API keys (phase 3) |
| G4 | **Neo4j indexes for new types** | `neo4j-migrations/001_indexes.cypher` covers older types only | Indexes for `phishingpage`, `phishkit`, `threatactor`, `registrar`, `isp` (+ any type missing today) |
| G5 | **Scan visible only after worker start** | `Scan` row is created *inside* the Celery task (`tasks/enricher.py:40`), so `GET /api/scans/{id}` 404s while queued | Either create the row at launch time in the API, or have the MCP treat 404 as `QUEUED` (v1: the latter, no API change) |

Already present and reused as-is: neighbours (`GET /sketches/{id}/nodes/{node_id}` →
`get_neighbors`), enricher launch returning `{"id": task_id}` where **task id == scan id**,
`GET /api/scans/{id}` with `status` / `details` / `error`.

### G1 — `POST /api/sketches/{sketch_id}/nodes/search`

```jsonc
// request
{
  "types": ["domain", "phishingpage"],   // optional; lowercase Neo4j labels
  "query": "rbfcu",                      // optional; case-insensitive substring on nodeLabel
  "filters": {"status": "VERIFIED"},     // optional; exact match on flat properties
  "limit": 50,                           // default 50, max 500
  "cursor": null                         // opaque; elementId-ordered keyset paging
}
// response
{ "nodes": [{"id": "4:…:12", "type": "domain", "label": "evil.example", "data": {…}}],
  "next_cursor": "…" | null,
  "total_estimate": 1234 }
```

Cypher sketch (in `GraphRepository.search_nodes`, exposed via `GraphService` and
`SketchService.search_nodes` with the usual `read` permission check):

```cypher
MATCH (n)
WHERE n.sketch_id = $sketch_id AND n.deleted_at IS NULL
  AND ($types IS NULL OR any(l IN labels(n) WHERE l IN $types))
  AND ($query IS NULL OR toLower(n.nodeLabel) CONTAINS toLower($query))
  AND all(k IN keys($filters) WHERE n[k] = $filters[k])
  AND ($cursor IS NULL OR elementId(n) > $cursor)
RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS data
ORDER BY elementId(n) LIMIT $limit
```

- `filters` keys must be validated against `^[A-Za-z0-9_.]+$` (nested props are stored
  flattened with `.` keys, `_build_node_query`).
- Reject `types` not in the type registry (+ user's custom types) with 400 — never
  interpolate them into Cypher.
- Later (not v1): `POST /api/investigations/{id}/nodes/search` across all sketches of an
  investigation — the "where else have we seen this IP?" query.

### G2 — `GET /api/sketches/{sketch_id}/stats`

```json
{"nodes": 5120, "edges": 9877,
 "by_type": {"domain": 1800, "ip": 950, "phishingpage": 2100, "asn": 41, "...": 0},
 "by_relationship": {"HOSTED_ON": 2100, "RESOLVES_TO": 3011}}
```

### G3 — Personal API keys (phase 3)

- Postgres table `api_tokens`: `id`, `owner_id` (FK profiles), `name`, `prefix` (first 8
  chars, for display), `token_hash` (SHA-256 of the full token), `created_at`,
  `last_used_at`, `expires_at` (nullable), `revoked_at`. Alembic migration in
  `flowsint-api/alembic`.
- Token format `fsk_<32 random urlsafe bytes>`; shown once at creation.
- Routes `/api/tokens`: `POST` (create), `GET` (list, prefix only), `DELETE /{id}` (revoke).
- `deps.get_current_user`: if the bearer starts with `fsk_`, hash + look up a non-revoked,
  non-expired row → its `Profile`; else existing JWT path. Update `last_used_at` at most
  once per minute.
- UI: a "API tokens" section in user settings (small; can follow later — tokens are
  creatable via the API meanwhile).
- Tokens act **as the user**: same investigation roles, no extra scopes in v1. Scopes
  (`read`, `write`, `enrich`) are a possible follow-up.

### G4 — Neo4j migration `005_threat_intel_indexes.cypher`

Same pattern as `001_indexes.cypher`: `sketch_id` and `nodeLabel` indexes for the five new
labels. Verify which existing types are also missing indexes and add them in the same file.

## Tool catalogue

Conventions:

- Every tool returns compact JSON text. Nodes are rendered as
  `{"ref": "<elementId>", "type": "domain", "label": "evil.example", "props": {…}}` with
  props truncated (strings > 300 chars cut, lists > 20 items cut, internal fields `x`, `y`,
  `sketch_id`, `created_at`, `nodeImage`, … dropped).
- **NodeRef**: wherever a tool takes a node, it accepts either an elementId string or
  `{"type": "Domain", "label": "evil.example"}`. `refs.py` resolves the latter via G1
  (`types=[type], query=label` then exact-match on label; error if 0 or >1 matches).
- Lists are paged (`limit`, `cursor`), default 25–50, hard max 200 in the MCP layer.
- Annotations (MCP `ToolAnnotations`) set on every tool: `readOnlyHint`,
  `destructiveHint=false`, `idempotentHint` where true, `openWorldHint` for enrichers
  (they call external services).

### Discovery (read-only)

| Tool | Args | API |
|---|---|---|
| `list_investigations` | — | `GET /api/investigations` |
| `list_sketches` | `investigation_id` | `GET /api/investigations/{id}/sketches` |
| `get_sketch` | `sketch_id` | `GET /api/sketches/{id}` + G2 stats |
| `list_types` | `category?` | `GET /api/types` — flattened to `type`, primary field, required fields |
| `list_enrichers` | `input_type?` | `GET /api/enrichers` — name, input/output type, description, params schema |
| `list_flows` | `input_type?` | `GET /api/flows` / `GET /api/flows/input_type/{t}` |

### Graph read (read-only)

| Tool | Args | API |
|---|---|---|
| `search_nodes` | `sketch_id, types?, query?, filters?, limit?, cursor?` | G1 |
| `get_node` | `sketch_id, node: NodeRef, neighbor_limit?` | `GET /sketches/{id}/nodes/{node_id}` — node + neighbours grouped by relationship label, truncated |
| `sketch_summary` | `sketch_id` | G2 — counts plus top 10 highest-degree nodes (add to G2 if cheap, else drop) |

### Graph write

| Tool | Args | API | Notes |
|---|---|---|---|
| `add_node` | `sketch_id, type, data` | `POST /sketches/{id}/nodes/add` | Idempotent (MERGE on type+label). Returns the node ref. |
| `add_relationship` | `sketch_id, source: NodeRef, target: NodeRef, label` | `POST /sketches/{id}/relations/add` | Label normalized to `UPPER_SNAKE_CASE`. |
| `edit_node` | `sketch_id, node: NodeRef, updates` | `PUT /sketches/{id}/nodes/edit` | Refuses changing the primary field (would change nodeLabel). |
| `import_entities` | `sketch_id, nodes[], edges[]` | `POST /sketches/{id}/import/execute` | Same record format as `bulk_ingest.py`; max 2,000 nodes per call; chunking logic shared/copied from the script. |

### Actions

| Tool | Args | API | Notes |
|---|---|---|---|
| `run_enricher` | `sketch_id, enricher, nodes: NodeRef[], params?, wait=true, timeout_s=120` | `POST /api/enrichers/{name}/launch` → poll `GET /api/scans/{id}` | Poll 1s→5s backoff. 404 ⇒ `QUEUED` (G5). Terminal: `COMPLETED`, `FAILED`. On completion return status + a **diff summary**: new node count by type (via G2 stats before/after) and up to 20 new node refs. With `wait=false` return `scan_id` immediately. |
| `run_flow` | `sketch_id, flow_id, nodes: NodeRef[], wait, timeout_s` | `POST /api/flows/{id}/launch` | Same polling. |
| `get_scan` | `scan_id` | `GET /api/scans/{id}` | Status, error, truncated details. |

### Resources & prompts (nice-to-have, phase 4)

- Resource `flowsint://types` — full type schema list (lets the client read it once
  instead of calling `list_types` repeatedly).
- Prompt `investigate_domain(domain, sketch_id)` — canned pivot plan: add → DNS → WHOIS →
  ASN → related pages, then summarize.

## Client behaviour (`client.py`)

- `httpx.AsyncClient(base_url=FLOWSINT_API_URL, timeout=…)`, one per server process
  (stdio) or per session (HTTP).
- Auth modes, chosen by config:
  1. `FLOWSINT_API_TOKEN` — sent as-is (JWT today, `fsk_…` after G3).
  2. `FLOWSINT_EMAIL` + `FLOWSINT_PASSWORD` — login at startup, re-login once on 401.
  3. HTTP transport: forward the **incoming** `Authorization: Bearer` header of the MCP
     request to the API (each user acts as themselves; the server holds no credentials).
- Retries: 3 attempts with backoff on connect errors and 502/503/504 only. Never retry
  POSTs that aren't idempotent (`relations/add` is MERGE-based → safe; enricher launch →
  **not** retried).
- Error mapping to MCP tool errors (`isError: true`) with actionable text:
  401 → "authentication failed, check token", 403 → "no access to this
  investigation/sketch", 404 → "not found", 400 → API `detail` verbatim, 5xx → "Flowsint
  API error" + detail.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `FLOWSINT_API_URL` | `http://localhost:5001` | API base URL |
| `FLOWSINT_API_TOKEN` | — | Bearer token (JWT or API key) |
| `FLOWSINT_EMAIL` / `FLOWSINT_PASSWORD` | — | Login fallback (stdio only) |
| `FLOWSINT_MCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `FLOWSINT_MCP_HOST` / `FLOWSINT_MCP_PORT` | `0.0.0.0` / `5002` | HTTP transport bind |
| `FLOWSINT_MCP_READ_ONLY` | `false` | Register only read-only tools |
| `FLOWSINT_MCP_ENRICH_TIMEOUT` | `120` | Default `run_enricher` wait |

Client registration example (Claude Code, `.mcp.json`):

```json
{"mcpServers": {"flowsint": {
  "command": "uv", "args": ["run", "--package", "flowsint-mcp", "flowsint-mcp"],
  "env": {"FLOWSINT_API_URL": "http://localhost:5001", "FLOWSINT_API_TOKEN": "fsk_…"}}}}
```

## Security considerations

- The MCP server never sees more than the token's user can see — all ACLs stay in the API.
- `FLOWSINT_MCP_READ_ONLY` for analysts who only want querying.
- Graph content is **untrusted data** (scraped page titles, WHOIS fields, Telegram
  messages). Tool descriptions state this; output keeps data inside JSON values, never as
  free text that reads like instructions. Prompt-injection via node properties is the main
  LLM-specific risk.
- Enrichers send node values to third parties (Shodan, WHOIS, …). `run_enricher` carries
  `openWorldHint=true` so clients can ask for confirmation.
- HTTP transport: bind behind the same reverse proxy/TLS as the API; no credentials stored
  server-side; validate `Origin` per MCP spec for browser-based clients.
- Phishkit `telegram_bot_tokens` are live secrets of the attacker — fine to return, but
  don't log tool outputs at INFO level.

## Testing

### Rule: every feature and every change ships with unit tests

No task in this plan is done without unit tests in the same commit. Concretely:

- **Every new function, endpoint, tool, model or migration** gets tests for the happy
  path, each documented error/edge case, and each permission outcome.
- **Every change to existing code** (e.g. `deps.get_current_user` in G3, repository
  methods) gets a test that fails without the change, so the behaviour is pinned.
- **Bugs found on the way** are fixed with a regression test (this is how the
  `DNSRecord` label key bug was found while testing the type registry categories).
- **Coverage gate**: `flowsint-mcp` runs `pytest --cov=flowsint_mcp
  --cov-fail-under=95` in `make test`/CI. New modules elsewhere (search/stats/tokens
  code paths) must be ≥ 95 % line-covered, measured with `--cov` on the touched modules
  in review. Exclusions only for genuinely unreachable lines, marked `# pragma: no cover`.
- Unit tests must not need running services: Neo4j and the API are faked
  (`pytest-httpx`, `MagicMock` repositories, SQLite session from `tests/conftest.py`).
  Anything that needs the real stack is an *integration* test, kept separate and
  optional.
- `make test` runs every suite (types, core, enrichers, api, scripts, app, and
  `flowsint-mcp` once it exists); `make lint` covers the new package.

### Required unit tests per component

| Component | Location | Must cover |
|---|---|---|
| `GraphRepository.search_nodes` | `flowsint-core/tests/repositories/` | Generated Cypher + params for each filter combination (types, query, filters, cursor, limit); soft-deleted excluded; invalid filter key rejected before reaching Cypher; limit clamped |
| `GraphRepository` stats | `flowsint-core/tests/repositories/` | Per-type and per-relationship counts mapping from query rows; empty sketch |
| `SketchService.search_nodes` / `get_stats` | `flowsint-core/tests/services/` | Owner/admin/editor/viewer allowed; no role → `PermissionDeniedError`; unknown sketch → `NotFoundError`; malformed id; unknown type → validation error; custom types resolved via the type registry |
| Search & stats routes | `flowsint-api/tests/` | Request validation (400s), 403/404 mapping, response shape, cursor round trip, `limit` > 500 rejected |
| API tokens (G3) | `flowsint-core/tests/services/`, `flowsint-api/tests/` | Token format; only hash stored; shown once; list shows prefix only; revoke; expired/revoked/unknown → 401; JWT path unchanged; `last_used_at` throttling; tokens can't access other users' investigations |
| Neo4j migration (G4) | `flowsint-core/tests/` | Every registered `FlowsintType` label has `sketch_id` and `nodeLabel` index statements (test parses the `.cypher` files against `TYPE_REGISTRY`, so a future type without an index fails CI) |
| `flowsint_mcp.config` | `flowsint-mcp/tests/` | Defaults, env overrides, invalid transport/port rejected, token vs. email/password precedence |
| `flowsint_mcp.client` | `flowsint-mcp/tests/` | Each auth mode; 401 → single re-login then error; retry only on connect errors/502/503/504; enricher launch never retried; error mapping 400/401/403/404/5xx → message; bearer pass-through in HTTP mode |
| `flowsint_mcp.refs` | `flowsint-mcp/tests/` | elementId passthrough; type+label → 0 / 1 / many matches; exact-match filtering over substring search results; type case normalisation |
| `flowsint_mcp.shaping` | `flowsint-mcp/tests/` | Internal fields dropped; long strings and lists truncated with markers; nested/flattened props; paging cursor passthrough; output size bound for a 1,000-node page |
| Every tool | `flowsint-mcp/tests/test_tools_*.py` | Correct API call(s) and arguments; result shape; error surfaces as `isError`; annotations (`readOnlyHint`, `openWorldHint`, …) set as specified |
| `run_enricher` / `run_flow` polling | `flowsint-mcp/tests/` | 404→QUEUED, RUNNING, COMPLETED, FAILED with error text, timeout returns `scan_id`, `wait=false`, backoff schedule (time mocked), stats diff summary |
| Read-only mode | `flowsint-mcp/tests/` | No write/action tools registered; read tools unaffected |
| Server wiring | `flowsint-mcp/tests/` | Tool list matches catalogue; stdio and HTTP transports start (in-process MCP client session against the server, API faked) |

### Beyond unit tests

- **End-to-end smoke** (manual, documented in README): `docker compose up`, register the
  server in Claude Code, run the scenario from *Why* on a sample sketch.
- MCP Inspector (`npx @modelcontextprotocol/inspector`) for manual tool checks.

## Phases & acceptance criteria

**Phase 1 — Read-only MCP + search** (API change + new package) — ✅ done

1. G1 search endpoint (repository → service → route) + tests.
2. G2 stats endpoint + tests.
3. G4 Neo4j index migration.
4. `flowsint-mcp` skeleton: config, client (token + email/password auth), shaping, stdio
   transport.
5. Tools: discovery set, `search_nodes`, `get_node`, `sketch_summary`.

*Done when*: unit tests for items 1–5 exist per the *Required unit tests* table,
`flowsint-mcp` coverage ≥ 95 %, `make test` and `make lint` are green, and from Claude
Code you can list investigations, open a sketch, find
`PhishingPage` nodes by brand substring, and inspect one with its neighbours — all with
outputs under ~4k tokens on a 50k-node sketch.

**Phase 2 — Write + actions** — ✅ done

6. NodeRef resolution.
7. `add_node`, `add_relationship`, `edit_node`, `import_entities`.
8. `run_enricher`, `run_flow`, `get_scan` with polling and diff summary.

*Done when*: unit tests for items 6–8 exist (refs, every write tool, polling state
machine), coverage gate still green, and the *Why* scenario runs end-to-end and the UI shows the new nodes live.

**Phase 3 — API keys + HTTP transport** — ✅ done

9. G3 API tokens (model, migration, routes, `get_current_user`), tests.
10. Streamable HTTP transport with bearer pass-through; Dockerfile; `docker-compose`
    service `mcp` on port 5002; docs page `docs/developers/mcp-server.mdx`.

*Done when*: token model/service/route/`get_current_user` tests pass (including the
unchanged JWT path), HTTP transport tests pass, and two users connect to one hosted MCP endpoint with their own keys and only
see their own investigations.

**Phase 4 — Polish (optional)** — ✅ done except item 12

Same rule: each item lands with its unit tests.

11. Investigation-wide search, resources/prompts, token scopes, UI for tokens.
12. Hub bridge tools or a Hub-backed enricher (e.g. `PhishingPage → Hub submission`).

## Open questions

1. **Hosting**: local-only (stdio per analyst) or shared HTTP endpoint for the team? Decides
   whether phase 3 is needed soon.
2. **Write access**: should assistants be allowed to write to sketches by default, or ship
   read-only by default and opt into writes?
3. **`flowsint_types` dependency**: validate `add_node` payloads client-side (better
   errors, version coupling) or rely on the API's validation (looser coupling)?
4. **Diff summary for enrichers**: stats before/after is cheap but racy if other users
   enrich the same sketch concurrently — acceptable for v1?
5. **G5**: leave the queued-404 workaround in the MCP, or fix it properly by creating the
   `Scan` row in the launch routes?
