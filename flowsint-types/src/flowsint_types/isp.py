from typing import List, Optional, Self

from pydantic import Field, model_validator

from .flowsint_base import FlowsintType
from .registry import flowsint_type


@flowsint_type
class ISP(FlowsintType):
    """Represents an internet service or hosting provider."""

    name: str = Field(
        ...,
        description="Provider name",
        title="Name",
        json_schema_extra={"primary": True},
    )
    asns: Optional[List[str]] = Field(
        None, description="Autonomous systems operated (e.g. AS13335)", title="ASNs"
    )
    url: Optional[str] = Field(None, description="Provider website", title="URL")
    abuse_email: Optional[str] = Field(
        None, description="Abuse contact email", title="Abuse Email"
    )
    abuse_phone: Optional[str] = Field(
        None, description="Abuse contact phone", title="Abuse Phone"
    )
    country: Optional[str] = Field(None, description="Country", title="Country")

    @model_validator(mode="after")
    def compute_label(self) -> Self:
        self.nodeLabel = self.name
        return self

    @classmethod
    def from_string(cls, line: str):
        """Parse an ISP from a raw string."""
        return cls(name=line.strip())

    @classmethod
    def detect(cls, line: str) -> bool:
        """ISP cannot be reliably detected from a single line of text."""
        return False
