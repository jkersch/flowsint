"""Shared fixtures: a fake Flowsint API (pytest-httpx) and an MCP session
connected in memory to the real server."""

import json
from typing import Any, Dict, List, Optional

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from flowsint_mcp.client import FlowsintClient
from flowsint_mcp.config import Settings
from flowsint_mcp.server import create_server

API = "http://api.test"

SKETCH = "11111111-1111-1111-1111-111111111111"


# Async tests (modules set `pytestmark = ANYIO`) run on AnyIO's plugin, which
# keeps a fixture and its test in one task; the MCP SDK's task groups break
# when setup and teardown happen in different tasks.
ANYIO = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Sleeps(list):
    """Records requested delays instead of waiting."""

    async def __call__(self, seconds: float) -> None:
        self.append(seconds)


@pytest.fixture
def settings() -> Settings:
    return Settings(api_url=API, api_token="tok")


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
def client(settings, sleeps) -> FlowsintClient:
    return FlowsintClient(settings, sleep=sleeps)


@pytest.fixture
async def session(settings, client):
    async with create_connected_server_and_client_session(
        create_server(settings, client)
    ) as s:
        yield s


async def call(session, tool: str, **arguments: Any) -> Dict[str, Any]:
    """Call a tool and return its JSON result; fail the test on a tool error."""
    result = await session.call_tool(tool, arguments)
    assert not result.isError, result.content[0].text
    return json.loads(result.content[0].text)


async def call_error(session, tool: str, **arguments: Any) -> str:
    """Call a tool that must fail and return its error text."""
    result = await session.call_tool(tool, arguments)
    assert result.isError, result.content[0].text
    return result.content[0].text


def gnode(node_id: str, node_type: str, label: str, **props: Any) -> Dict[str, Any]:
    """A node as the API returns it (GraphNode)."""
    return {
        "id": node_id,
        "nodeType": node_type,
        "nodeLabel": label,
        "nodeProperties": {"nodeLabel": label, **props},
        "nodeMetadata": {},
        "x": 1.0,
        "y": 2.0,
        "nodeColor": None,
    }


def search_page(nodes: List[Dict[str, Any]], cursor: Optional[str] = None):
    return {"nodes": nodes, "next_cursor": cursor}


def sent_json(request) -> Any:
    return json.loads(request.content)


TYPES = [
    {
        "type": "network_category",
        "key": "network",
        "label": "Network",
        "children": [
            {
                "type": "Domain",
                "label_key": "domain",
                "description": "Represents a domain name.\nMore text.",
                "fields": [
                    {"name": "domain", "required": True},
                    {"name": "root", "required": False},
                ],
            },
            {
                "type": "Ip",
                "label_key": "address",
                "description": "",
                "fields": [{"name": "address", "required": True}],
            },
        ],
    },
    {
        "type": "threat_intel_category",
        "key": "threat_intel",
        "label": "Threat Intelligence",
        "children": [
            {
                "type": "PhishingPage",
                "label_key": "url",
                "description": "Represents a phishing page.",
                "fields": [
                    {"name": "url", "required": True},
                    {"name": "status", "required": False},
                ],
            }
        ],
    },
    {
        "type": "person_category",
        "key": "person_category",
        "label": "Identities & Entities",
        "children": [
            {
                "type": "Domain",
                "label_key": "domain",
                "description": "",
                "fields": [],
            }
        ],
    },
]
