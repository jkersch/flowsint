"""Which API-token scope a request needs.

Decided centrally from the method and path, so a new endpoint is covered the
moment it exists: anything not known to be a read or an enrichment counts as
a write, the safe default.
"""

import re

READ = "read"
WRITE = "write"
ENRICH = "enrich"

READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# POST endpoints that only read or compute; they change nothing.
READ_POSTS = [
    re.compile(p)
    for p in (
        r"^/api/sketches/[^/]+/nodes/search$",
        r"^/api/investigations/[^/]+/nodes/search$",
        r"^/api/sketches/[^/]+/import/analyze$",
        r"^/api/types/detect$",
        r"^/api/flows/[^/]+/compute$",
        r"^/api/custom-types/[^/]+/validate$",
    )
]

# Endpoints that send data to external services or run enrichers.
ENRICH_POSTS = [
    re.compile(p)
    for p in (
        r"^/api/enrichers/(?!templates/)[^/]+/launch$",
        r"^/api/flows/[^/]+/launch$",
        r"^/api/enrichers/templates/[^/]+/test$",
    )
]


def required_scope(method: str, path: str) -> str:
    method = method.upper()
    path = path.rstrip("/") or "/"
    if method in READ_METHODS:
        return READ
    if method == "POST":
        if any(p.match(path) for p in ENRICH_POSTS):
            return ENRICH
        if any(p.match(path) for p in READ_POSTS):
            return READ
    return WRITE
