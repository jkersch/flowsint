"""Tools to find investigations, sketches, types, enrichers and flows."""

from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..client import FlowsintClient
from ..shaping import first_line, truncate

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def _sketch(sketch: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": sketch.get("id"),
        "title": sketch.get("title"),
        "description": first_line(sketch.get("description")),
        "status": sketch.get("status"),
        "last_updated_at": sketch.get("last_updated_at"),
    }


def _io_type(schema: Any) -> Optional[str]:
    return schema.get("type") if isinstance(schema, dict) else None


def summarize_types(
    categories: List[Dict[str, Any]], category: Optional[str] = None
) -> Dict[str, Any]:
    """One entry per type (merging the categories it appears in)."""
    wanted = category.casefold() if category else None
    types: Dict[str, Dict[str, Any]] = {}
    for cat in categories:
        names = {
            str(cat.get("label", "")).casefold(),
            str(cat.get("key", "")).casefold(),
        }
        if wanted and wanted not in names:
            continue
        for child in cat.get("children") or []:
            entry = types.setdefault(
                child["type"],
                {
                    "type": child["type"],
                    "description": first_line(child.get("description")),
                    "primary_field": child.get("label_key"),
                    "fields": [
                        f["name"] + (" (required)" if f.get("required") else "")
                        for f in child.get("fields") or []
                    ],
                    "categories": [],
                },
            )
            entry["categories"].append(cat.get("label"))
    return {"types": list(types.values())}


def register(mcp: FastMCP, client: FlowsintClient) -> None:
    @mcp.tool(annotations=READ)
    async def list_investigations() -> Dict[str, Any]:
        """List the investigations you have access to, with their sketches.

        A sketch is one graph inside an investigation; most other tools take a
        sketch_id from here.
        """
        investigations = await client.list_investigations()
        return {
            "investigations": [
                {
                    "id": inv.get("id"),
                    "name": inv.get("name"),
                    "description": first_line(inv.get("description")),
                    "status": inv.get("status"),
                    "your_role": inv.get("current_user_role"),
                    "last_updated_at": inv.get("last_updated_at"),
                    "sketches": [
                        {"id": s.get("id"), "title": s.get("title")}
                        for s in inv.get("sketches") or []
                    ],
                }
                for inv in investigations
            ]
        }

    @mcp.tool(annotations=READ)
    async def list_sketches(investigation_id: str) -> Dict[str, Any]:
        """List the sketches (graphs) of one investigation."""
        sketches = await client.list_sketches(investigation_id)
        return {"sketches": [_sketch(s) for s in sketches]}

    @mcp.tool(annotations=READ)
    async def get_sketch(sketch_id: str) -> Dict[str, Any]:
        """Show a sketch's details and how many nodes of each type it holds."""
        sketch = await client.get_sketch(sketch_id)
        stats = await client.get_stats(sketch_id)
        return {
            "sketch": {
                **_sketch(sketch),
                "investigation_id": sketch.get("investigation_id"),
            },
            "nodes": stats.get("nodes"),
            "edges": stats.get("edges"),
            "nodes_by_type": stats.get("by_type"),
        }

    @mcp.tool(annotations=READ)
    async def list_types(category: Optional[str] = None) -> Dict[str, Any]:
        """List the entity types you can add to a sketch and their fields.

        `primary_field` identifies an entity; give it when adding a node.
        Optionally filter by category name (e.g. 'Network').
        """
        return summarize_types(await client.list_types(), category)

    @mcp.tool(annotations=READ)
    async def list_enrichers(input_type: Optional[str] = None) -> Dict[str, Any]:
        """List enrichers (automated pivots), optionally only those that accept
        a given input type such as 'Domain'.

        Run one with run_enricher. Enrichers may query third-party services.
        """
        enrichers: List[Dict[str, Any]] = await client.list_enrichers()
        wanted = input_type.casefold() if input_type else None
        result = []
        for e in enrichers:
            source = _io_type(e.get("inputs"))
            if wanted and (source or "").casefold() != wanted:
                continue
            result.append(
                {
                    "name": e.get("name"),
                    "description": first_line(e.get("description")),
                    "input_type": source,
                    "output_type": _io_type(e.get("outputs")),
                    "params": [
                        {
                            "name": p.get("name"),
                            "type": p.get("type"),
                            "required": bool(p.get("required")),
                            "description": truncate(p.get("description")),
                        }
                        for p in e.get("params_schema") or []
                    ],
                }
            )
        return {"enrichers": result}

    @mcp.tool(annotations=READ)
    async def list_flows() -> Dict[str, Any]:
        """List saved flows (chains of enrichers). Run one with run_flow."""
        flows = await client.list_flows()
        return {
            "flows": [
                {
                    "id": f.get("id"),
                    "name": f.get("name"),
                    "description": first_line(f.get("description")),
                    "input_types": f.get("category") or [],
                }
                for f in flows
            ]
        }
