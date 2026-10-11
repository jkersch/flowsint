from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class NodeSearchInput(BaseModel):
    types: Optional[List[str]] = Field(
        None, description="Node types to match (any of them), e.g. ['Domain']"
    )
    query: Optional[str] = Field(
        None, description="Case-insensitive substring of the node label"
    )
    filters: Optional[Dict[str, Any]] = Field(
        None, description="Exact matches on type fields, e.g. {'status': 'VERIFIED'}"
    )
    limit: int = Field(50, ge=1, le=500)
    cursor: Optional[str] = Field(None, description="next_cursor of the previous page")
