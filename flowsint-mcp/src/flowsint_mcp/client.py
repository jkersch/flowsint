"""Async client for the Flowsint REST API.

Every call runs as the configured user, so the API's own permission checks
decide what the MCP server may read or change.
"""

import asyncio
import json
from typing import Any, Awaitable, Callable, Dict, List, Optional, cast
from urllib.parse import quote

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.lowlevel.server import request_ctx

from .config import Settings

RETRY_STATUSES = {502, 503, 504}


class FlowsintAPIError(ToolError):
    """An API call failed; the message is meant for the model and the user."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail", response.text)
    except (ValueError, AttributeError):
        return response.text[:500]
    if isinstance(detail, list):  # FastAPI request validation errors
        return "; ".join(
            f"{'.'.join(str(p) for p in err.get('loc', [])[1:])}: {err.get('msg')}"
            for err in detail
        )
    return str(detail)


def _error(response: httpx.Response) -> FlowsintAPIError:
    status, detail = response.status_code, _detail(response)
    if status == 401:
        message = (
            "Authentication failed: the Flowsint token is missing, expired or revoked."
        )
    elif status == 403:
        message = (
            "No access: your Flowsint user lacks the required role on this "
            "investigation or sketch."
        )
        if detail and detail != "Forbidden":  # e.g. a missing token scope
            message = f"No access: {detail}."
    elif status == 404:
        message = f"Not found: {detail}"
    elif status in (400, 422):
        message = f"Invalid request: {detail}"
    else:
        message = f"Flowsint API error ({status}): {detail}"
    return FlowsintAPIError(message, status)


class FlowsintClient:
    def __init__(
        self,
        settings: Settings,
        http: Optional[httpx.AsyncClient] = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retries: int = 3,
    ):
        self._settings = settings
        self._http = http or httpx.AsyncClient(base_url=settings.api_url, timeout=60)
        self._token = settings.api_token
        self._sleep = sleep
        self._retries = retries

    async def aclose(self) -> None:
        await self._http.aclose()

    async def login(self) -> None:
        try:
            response = await self._http.post(
                "/api/auth/token",
                data={
                    "username": self._settings.email,
                    "password": self._settings.password,
                },
            )
        except httpx.TransportError as e:
            raise FlowsintAPIError(self._unreachable(e)) from e
        if response.status_code != 200:
            raise FlowsintAPIError(
                "Login failed: check FLOWSINT_EMAIL and FLOWSINT_PASSWORD.",
                response.status_code,
            )
        self._token = response.json()["access_token"]

    def _bearer(self) -> str:
        """The token to send: the caller's own in HTTP mode, else ours."""
        if not self._settings.passthrough:
            return f"Bearer {self._token}"
        try:
            request = request_ctx.get().request
        except LookupError:
            request = None
        header = request.headers.get("authorization", "") if request else ""
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise FlowsintAPIError(
                "Authentication failed: send your Flowsint API token as "
                "'Authorization: Bearer <token>'.",
                401,
            )
        return f"Bearer {token.strip()}"

    def _unreachable(self, error: Exception) -> str:
        return f"Cannot reach the Flowsint API at {self._settings.api_url}: {error}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        form: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        idempotent: bool = True,
    ) -> Any:
        """Send a request; retry transient failures only if it's safe to repeat.

        A 401 triggers one re-login when email/password are configured: the
        API rejected the request before doing anything, so even a
        non-idempotent call can be resent.
        """
        if self._token is None and self._settings.can_login:
            await self.login()

        attempt, relogged = 0, False
        while True:
            try:
                response = await self._http.request(
                    method,
                    path,
                    json=json_body,
                    data=form,
                    params=params,
                    headers={"Authorization": self._bearer()},
                )
            except httpx.TransportError as e:
                if idempotent and attempt < self._retries:
                    attempt += 1
                    await self._sleep(0.5 * 2 ** (attempt - 1))
                    continue
                raise FlowsintAPIError(self._unreachable(e)) from e

            if (
                response.status_code == 401
                and not relogged
                and self._settings.can_login
            ):
                relogged = True
                await self.login()
                continue
            if (
                response.status_code in RETRY_STATUSES
                and idempotent
                and attempt < self._retries
            ):
                attempt += 1
                await self._sleep(0.5 * 2 ** (attempt - 1))
                continue
            if response.status_code >= 400:
                raise _error(response)
            if not response.content:
                return None
            return response.json()

    async def _object(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        return cast(Dict[str, Any], await self.request(method, path, **kwargs))

    async def _list(
        self, method: str, path: str, **kwargs: Any
    ) -> List[Dict[str, Any]]:
        return cast(List[Dict[str, Any]], await self.request(method, path, **kwargs))

    # --- discovery -----------------------------------------------------------

    async def list_investigations(self) -> List[Dict[str, Any]]:
        return await self._list("GET", "/api/investigations")

    async def list_sketches(self, investigation_id: str) -> List[Dict[str, Any]]:
        return await self._list(
            "GET", f"/api/investigations/{_seg(investigation_id)}/sketches"
        )

    async def get_sketch(self, sketch_id: str) -> Dict[str, Any]:
        return await self._object("GET", f"/api/sketches/{_seg(sketch_id)}")

    async def list_types(self) -> List[Dict[str, Any]]:
        return await self._list("GET", "/api/types")

    async def list_enrichers(self) -> List[Dict[str, Any]]:
        return await self._list("GET", "/api/enrichers")

    async def list_flows(self) -> List[Dict[str, Any]]:
        return await self._list("GET", "/api/flows")

    # --- graph read ----------------------------------------------------------

    async def get_stats(self, sketch_id: str) -> Dict[str, Any]:
        return await self._object("GET", f"/api/sketches/{_seg(sketch_id)}/stats")

    async def search_nodes(self, sketch_id: str, **criteria: Any) -> Dict[str, Any]:
        body = {k: v for k, v in criteria.items() if v is not None}
        return await self._object(
            "POST", f"/api/sketches/{_seg(sketch_id)}/nodes/search", json_body=body
        )

    async def search_investigation(
        self, investigation_id: str, **criteria: Any
    ) -> Dict[str, Any]:
        body = {k: v for k, v in criteria.items() if v is not None}
        return await self._object(
            "POST",
            f"/api/investigations/{_seg(investigation_id)}/nodes/search",
            json_body=body,
        )

    async def get_neighbors(self, sketch_id: str, node_id: str) -> Dict[str, Any]:
        return await self._object(
            "GET", f"/api/sketches/{_seg(sketch_id)}/nodes/{_seg(node_id)}"
        )

    # --- graph write (all MERGE/SET based, so safe to retry) ------------------

    async def import_entities(
        self,
        sketch_id: str,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        payload = {"nodes": nodes, "edges": edges}
        return await self._object(
            "POST",
            f"/api/sketches/{_seg(sketch_id)}/import/execute",
            form={"entity_mappings_json": json.dumps(payload)},
        )

    async def add_relationship(
        self, sketch_id: str, source: str, target: str, label: str
    ) -> Dict[str, Any]:
        return await self._object(
            "POST",
            f"/api/sketches/{_seg(sketch_id)}/relations/add",
            json_body={
                "source": source,
                "target": target,
                "type": "one-way",
                "label": label,
            },
        )

    async def edit_node(
        self, sketch_id: str, node_id: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await self._object(
            "PUT",
            f"/api/sketches/{_seg(sketch_id)}/nodes/edit",
            json_body={"nodeId": node_id, "updates": updates},
        )

    # --- actions (queue work: never retried) ---------------------------------

    async def launch_enricher(
        self,
        name: str,
        sketch_id: str,
        node_ids: List[str],
        params: Optional[Dict[str, Any]] = None,
    ) -> str:
        result = await self._object(
            "POST",
            f"/api/enrichers/{_seg(name)}/launch",
            json_body={
                "node_ids": node_ids,
                "sketch_id": sketch_id,
                "params": params or {},
            },
            idempotent=False,
        )
        return str(result["id"])

    async def launch_flow(
        self, flow_id: str, sketch_id: str, node_ids: List[str]
    ) -> str:
        result = await self._object(
            "POST",
            f"/api/flows/{_seg(flow_id)}/launch",
            json_body={"node_ids": node_ids, "sketch_id": sketch_id},
            idempotent=False,
        )
        return str(result["id"])

    async def get_scan(self, scan_id: str) -> Optional[Dict[str, Any]]:
        """The scan, or None while it's still queued.

        The worker creates the scan record when it picks the task up, so a
        freshly launched scan is a 404 until then.
        """
        try:
            return await self._object("GET", f"/api/scans/{_seg(scan_id)}")
        except FlowsintAPIError as e:
            if e.status == 404:
                return None
            raise


def _seg(value: str) -> str:
    """Quote one path segment (element ids contain ':')."""
    return quote(str(value), safe="")
