from typing import Optional, Self

from pydantic import Field, model_validator

from .flowsint_base import FlowsintType
from .registry import flowsint_type


@flowsint_type
class Registrar(FlowsintType):
    """Represents a domain name registrar."""

    name: str = Field(
        ...,
        description="Registrar name",
        title="Name",
        json_schema_extra={"primary": True},
    )
    iana_id: Optional[int] = Field(
        None, description="IANA registrar ID", title="IANA ID", ge=0
    )
    url: Optional[str] = Field(None, description="Registrar website", title="URL")
    whois_server: Optional[str] = Field(
        None, description="Registrar WHOIS server", title="WHOIS Server"
    )
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
        """Parse a registrar from a raw string."""
        return cls(name=line.strip())

    @classmethod
    def detect(cls, line: str) -> bool:
        """Registrar cannot be reliably detected from a single line of text."""
        return False
