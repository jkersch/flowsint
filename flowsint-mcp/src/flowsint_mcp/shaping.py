"""Compact, size-bounded renderings of API objects for tool output.

Graph data can be large and partly attacker-controlled (scraped titles,
WHOIS fields), so everything is truncated and kept inside JSON values.
"""

from typing import Any, Dict, List, Optional

MAX_STRING = 300
MAX_LIST = 20
MAX_DEPTH = 4


def truncate(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        if len(value) > MAX_STRING:
            return f"{value[:MAX_STRING]}… [+{len(value) - MAX_STRING} chars]"
        return value
    if isinstance(value, list):
        items = [truncate(v, depth + 1) for v in value[:MAX_LIST]]
        if len(value) > MAX_LIST:
            items.append(f"… [+{len(value) - MAX_LIST} more]")
        return items
    if isinstance(value, dict):
        if depth >= MAX_DEPTH:
            return "{…}"
        return {k: truncate(v, depth + 1) for k, v in value.items()}
    return value


def _clean(props: Any) -> Dict[str, Any]:
    if not isinstance(props, dict):
        return {}
    return {
        k: v
        for k, v in props.items()
        if v not in (None, [], {}, "") and k != "nodeLabel"
    }


def compact_node(node: Dict[str, Any]) -> Dict[str, Any]:
    """{ref, type, label, props} from an API GraphNode."""
    return {
        "ref": node.get("id"),
        "type": node.get("nodeType"),
        "label": node.get("nodeLabel"),
        "props": truncate(_clean(node.get("nodeProperties"))),
    }


def first_line(text: Optional[str]) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return str(truncate(line.strip()))
    return ""


def compact_list(items: List[Any], limit: int) -> Dict[str, Any]:
    """Cap a list, saying how much was left out."""
    result: Dict[str, Any] = {"items": items[:limit]}
    if len(items) > limit:
        result["omitted"] = len(items) - limit
    return result
