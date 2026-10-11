from typing import List, Optional, Self

from pydantic import Field, model_validator

from .flowsint_base import FlowsintType
from .registry import flowsint_type


@flowsint_type
class ThreatActor(FlowsintType):
    """Represents a threat actor or group behind malicious activity."""

    name: str = Field(
        ...,
        description="Threat actor name or handle",
        title="Name",
        json_schema_extra={"primary": True},
    )
    aliases: Optional[List[str]] = Field(
        None, description="Other known names or handles", title="Aliases"
    )
    description: Optional[str] = Field(
        None, description="Analyst notes", title="Description"
    )
    country: Optional[str] = Field(
        None, description="Suspected country of origin", title="Country"
    )
    motivation: Optional[str] = Field(
        None, description="Motivation (financial, espionage, ...)", title="Motivation"
    )
    telegram_handles: Optional[List[str]] = Field(
        None, description="Telegram handles or channels", title="Telegram Handles"
    )
    first_seen: Optional[str] = Field(
        None, description="First time the actor was observed", title="First Seen"
    )
    last_seen: Optional[str] = Field(
        None, description="Last time the actor was observed", title="Last Seen"
    )
    source: Optional[str] = Field(
        None, description="Source of the attribution", title="Source"
    )

    @model_validator(mode="after")
    def compute_label(self) -> Self:
        self.nodeLabel = self.name
        return self

    @classmethod
    def from_string(cls, line: str):
        """Parse a threat actor from a raw string."""
        return cls(name=line.strip())

    @classmethod
    def detect(cls, line: str) -> bool:
        """Threat actor cannot be reliably detected from a single line of text."""
        return False
