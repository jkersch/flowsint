"""Resolve human-friendly node references to element ids."""

from typing import Union

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from .client import FlowsintClient

SEARCH_PAGE = 500
SEARCH_MAX_PAGES = 5


class NodeKey(BaseModel):
    """A node named by its type and label, e.g. Domain / evil.example."""

    type: str = Field(description="Flowsint type, e.g. 'Domain' or 'PhishingPage'")
    label: str = Field(description="The node's label as shown in the graph")


NodeRef = Union[str, NodeKey]


class NodeRefError(ToolError):
    """A node reference matched no node or several."""


async def resolve(client: FlowsintClient, sketch_id: str, ref: NodeRef) -> str:
    if isinstance(ref, str):
        if not ref.strip():
            raise NodeRefError("A node reference must not be empty")
        return ref.strip()

    exact, folded = [], []
    cursor = None
    for _ in range(SEARCH_MAX_PAGES):
        page = await client.search_nodes(
            sketch_id,
            types=[ref.type],
            query=ref.label,
            limit=SEARCH_PAGE,
            cursor=cursor,
        )
        for node in page["nodes"]:
            if node["nodeLabel"] == ref.label:
                exact.append(node["id"])
            elif node["nodeLabel"].casefold() == ref.label.casefold():
                folded.append(node["id"])
        cursor = page.get("next_cursor")
        if not cursor:
            break

    matches = exact or folded
    if len(matches) == 1:
        return str(matches[0])
    if not matches:
        raise NodeRefError(
            f"No {ref.type} node labelled {ref.label!r} in this sketch. "
            "Use search_nodes to find the right label or ref."
        )
    raise NodeRefError(
        f"{len(matches)} {ref.type} nodes are labelled {ref.label!r}; "
        f"pass one of these refs instead: {', '.join(matches[:10])}"
    )
