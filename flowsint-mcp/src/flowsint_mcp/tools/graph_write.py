"""Tools that add to or change a sketch. Nothing here deletes."""

import re
from typing import Annotated, Any, Dict, List

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ..client import FlowsintClient
from ..refs import NodeRef, resolve
from ..shaping import compact_list, truncate

# Writes merge on (type, label), so repeating one changes nothing new.
WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
MAX_IMPORT_NODES = 2000
MAX_REPORTED_NODES = 100


class NewNode(BaseModel):
    ref: str = Field(description="Your local id for this node, used by edges")
    type: str = Field(description="Flowsint type, e.g. 'Domain'")
    data: Dict[str, Any] = Field(description="The type's fields")


class NewEdge(BaseModel):
    source: str = Field(description="ref of a node in this call")
    target: str = Field(description="ref of a node in this call")
    label: str = Field(description="Relationship, e.g. HOSTED_ON")


def relationship_label(label: str) -> str:
    """'hosted on' -> 'HOSTED_ON'; the convention used across Flowsint."""
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").upper()
    if not normalized:
        raise ToolError("A relationship label needs at least one letter or digit")
    return normalized


def _mapping(node: NewNode) -> Dict[str, Any]:
    return {
        "id": node.ref,
        "node_id": node.ref,
        "entity_type": node.type,
        "nodeLabel": node.ref,
        "include": True,
        "data": node.data,
    }


async def _primary_field(client: FlowsintClient, node_type: str) -> str | None:
    for category in await client.list_types():
        for child in category.get("children") or []:
            if child["type"].lower() == node_type.lower():
                label_key = child.get("label_key")
                return str(label_key) if label_key else None
    return None


def register(mcp: FastMCP, client: FlowsintClient) -> None:
    @mcp.tool(annotations=WRITE)
    async def add_node(
        sketch_id: str,
        type: Annotated[str, Field(description="Flowsint type, see list_types")],
        data: Annotated[
            Dict[str, Any],
            Field(description="Field values; must include the primary field"),
        ],
    ) -> Dict[str, Any]:
        """Add an entity to a sketch, or update it if it already exists.

        Returns its ref and the label Flowsint computed for it.
        """
        result = await client.import_entities(
            sketch_id, [_mapping(NewNode(ref="node", type=type, data=data))], []
        )
        if not result.get("nodes"):
            errors = "; ".join(result.get("errors") or ["unknown error"])
            raise ToolError(f"Could not add the {type} node: {errors}")
        written = result["nodes"][0]
        return {
            "node": {
                "ref": written["id"],
                "type": written["type"],
                "label": written["label"],
            }
        }

    @mcp.tool(annotations=WRITE)
    async def add_relationship(
        sketch_id: str,
        source: NodeRef,
        target: NodeRef,
        label: Annotated[
            str, Field(description="Verb phrase, e.g. 'resolves to' or RESOLVES_TO")
        ],
    ) -> Dict[str, Any]:
        """Link two nodes with a directed relationship (source -> target)."""
        rel = relationship_label(label)
        source_id = await resolve(client, sketch_id, source)
        target_id = await resolve(client, sketch_id, target)
        await client.add_relationship(sketch_id, source_id, target_id, rel)
        return {"source": source_id, "target": target_id, "label": rel}

    @mcp.tool(annotations=WRITE)
    async def edit_node(
        sketch_id: str,
        node: NodeRef,
        updates: Annotated[
            Dict[str, Any],
            Field(description="Fields to set, e.g. {'status': 'NEUTRALIZED'}"),
        ],
    ) -> Dict[str, Any]:
        """Set fields on an existing node.

        The primary field (what identifies the entity) can't be changed; add a
        new node instead.
        """
        if not updates:
            raise ToolError("Nothing to update")
        node_id = await resolve(client, sketch_id, node)
        center = next(
            n
            for n in (await client.get_neighbors(sketch_id, node_id))["nds"]
            if n["id"] == node_id
        )
        primary = await _primary_field(client, center["nodeType"])
        if primary and primary in updates:
            raise ToolError(
                f"'{primary}' identifies this {center['nodeType']} and can't be "
                "changed; add a new node instead"
            )
        await client.edit_node(sketch_id, node_id, {"nodeProperties": updates})
        return {"node": node_id, "updated": sorted(updates)}

    @mcp.tool(annotations=WRITE)
    async def import_entities(
        sketch_id: str,
        nodes: Annotated[
            List[NewNode], Field(max_length=MAX_IMPORT_NODES, min_length=1)
        ],
        edges: List[NewEdge] = [],
    ) -> Dict[str, Any]:
        """Add many entities and their relationships in one call.

        Edges link nodes of the same call by their `ref`. Existing entities are
        updated rather than duplicated. For very large loads use the bulk
        ingest script instead.
        """
        refs = [n.ref for n in nodes]
        duplicates = sorted({r for r in refs if refs.count(r) > 1})
        if duplicates:
            raise ToolError(f"Duplicate node refs: {', '.join(duplicates)}")
        unknown = sorted({r for e in edges for r in (e.source, e.target)} - set(refs))
        if unknown:
            raise ToolError(f"Edges reference unknown refs: {', '.join(unknown)}")

        result = await client.import_entities(
            sketch_id,
            [_mapping(n) for n in nodes],
            [
                {
                    "from_id": e.source,
                    "to_id": e.target,
                    "label": relationship_label(e.label),
                }
                for e in edges
            ],
        )
        written = [
            {"ref": n["ref"], "node": n["id"], "label": n["label"]}
            for n in result.get("nodes") or []
        ]
        return {
            "status": result.get("status"),
            "nodes_written": result.get("nodes_created"),
            "nodes": compact_list(written, MAX_REPORTED_NODES),
            "errors": truncate(result.get("errors") or []),
        }
