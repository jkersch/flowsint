"""Tools that run enrichers and flows and follow their scans."""

import asyncio
import time
from typing import Annotated, Any, Awaitable, Callable, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from ..client import FlowsintClient
from ..refs import NodeRef, resolve
from ..shaping import truncate
from .discovery import READ

# Enrichers query third-party services and write results into the sketch.
RUN = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
TERMINAL = {"COMPLETED", "FAILED"}
POLL_DELAYS = [1.0, 1.0, 2.0, 3.0, 5.0]  # then 5s each

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


def _scan_view(scan_id: str, scan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if scan is None:
        return {"scan_id": scan_id, "status": "QUEUED"}
    view: Dict[str, Any] = {"scan_id": scan_id, "status": scan.get("status")}
    if scan.get("error"):
        view["error"] = truncate(scan["error"])
    if scan.get("details") is not None:
        view["details"] = truncate(scan["details"])
    return view


def _diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    old, new = before.get("by_type") or {}, after.get("by_type") or {}
    added = {t: new[t] - old.get(t, 0) for t in new if new[t] > old.get(t, 0)}
    return {
        "new_nodes_by_type": added,
        "new_edges": max(0, (after.get("edges") or 0) - (before.get("edges") or 0)),
    }


class ScanRunner:
    """Launch work, then poll its scan until it ends or time runs out."""

    def __init__(
        self,
        client: FlowsintClient,
        default_timeout: float,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
    ):
        self.client = client
        self.default_timeout = default_timeout
        self.sleep = sleep
        self.clock = clock

    async def run(
        self,
        sketch_id: str,
        launch: Callable[[], Awaitable[str]],
        wait: bool,
        timeout_s: Optional[float],
    ) -> Dict[str, Any]:
        before = await self.client.get_stats(sketch_id) if wait else None
        scan_id = await launch()
        if not wait:
            return {"scan_id": scan_id, "status": "QUEUED"}

        deadline = self.clock() + (timeout_s or self.default_timeout)
        attempt = 0
        while True:
            scan = await self.client.get_scan(scan_id)
            view = _scan_view(scan_id, scan)
            if view["status"] in TERMINAL:
                break
            if self.clock() >= deadline:
                view["message"] = "Still running; check later with get_scan(scan_id)."
                return view
            await self.sleep(POLL_DELAYS[min(attempt, len(POLL_DELAYS) - 1)])
            attempt += 1

        if view["status"] == "COMPLETED":
            view.update(_diff(before or {}, await self.client.get_stats(sketch_id)))
        return view


def register_read_only(mcp: FastMCP, client: FlowsintClient) -> None:
    @mcp.tool(annotations=READ)
    async def get_scan(scan_id: str) -> Dict[str, Any]:
        """Check an enricher or flow run started with wait=false or that timed
        out. Status is QUEUED, PENDING, RUNNING, COMPLETED or FAILED."""
        return _scan_view(scan_id, await client.get_scan(scan_id))


def register(
    mcp: FastMCP,
    client: FlowsintClient,
    default_timeout: float,
    sleep: Sleep = asyncio.sleep,
    clock: Clock = time.monotonic,
) -> None:
    runner = ScanRunner(client, default_timeout, sleep=sleep, clock=clock)
    register_read_only(mcp, client)

    async def resolve_all(sketch_id: str, nodes: List[NodeRef]) -> List[str]:
        if not nodes:
            raise ToolError("Give at least one node to run on")
        return [await resolve(client, sketch_id, n) for n in nodes]

    @mcp.tool(annotations=RUN)
    async def run_enricher(
        sketch_id: str,
        enricher: Annotated[str, Field(description="Name from list_enrichers")],
        nodes: Annotated[
            List[NodeRef], Field(description="Nodes of the enricher's input type")
        ],
        params: Optional[Dict[str, Any]] = None,
        wait: Annotated[
            bool, Field(description="Wait for the result (default) or return at once")
        ] = True,
        timeout_s: Annotated[Optional[float], Field(gt=0, le=1800)] = None,
    ) -> Dict[str, Any]:
        """Run an enricher on nodes; its findings are added to the sketch.

        When it completes, reports how many nodes and edges it added. The
        enricher may send the node values to third-party services.
        """
        node_ids = await resolve_all(sketch_id, nodes)
        return await runner.run(
            sketch_id,
            lambda: client.launch_enricher(enricher, sketch_id, node_ids, params),
            wait,
            timeout_s,
        )

    @mcp.tool(annotations=RUN)
    async def run_flow(
        sketch_id: str,
        flow_id: Annotated[str, Field(description="id from list_flows")],
        nodes: List[NodeRef],
        wait: bool = True,
        timeout_s: Annotated[Optional[float], Field(gt=0, le=1800)] = None,
    ) -> Dict[str, Any]:
        """Run a saved flow (a chain of enrichers) on nodes."""
        node_ids = await resolve_all(sketch_id, nodes)
        return await runner.run(
            sketch_id,
            lambda: client.launch_flow(flow_id, sketch_id, node_ids),
            wait,
            timeout_s,
        )
