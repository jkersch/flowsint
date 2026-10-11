from typing import List, Optional, Self

from pydantic import Field, field_validator, model_validator

from .flowsint_base import FlowsintType
from .registry import flowsint_type


@flowsint_type
class PhishingPage(FlowsintType):
    """Represents a phishing (or scam/malware) page identified by its URL."""

    url: str = Field(
        ...,
        description="Full URL of the phishing page",
        title="URL",
        json_schema_extra={"primary": True},
    )
    domain: Optional[str] = Field(
        None, description="Domain hosting the page", title="Domain"
    )
    category: Optional[str] = Field(
        None,
        description="Kind of abuse (PHISHING, MALWARE, SCAM, ...)",
        title="Category",
    )
    status: Optional[str] = Field(
        None,
        description="Lifecycle status (PENDING, VERIFIED, TAKEDOWN, NEUTRALIZED, ...)",
        title="Status",
    )
    target_brands: Optional[List[str]] = Field(
        None, description="Brands or organizations impersonated", title="Target Brands"
    )
    title: Optional[str] = Field(None, description="Page title", title="Title")
    screenshot_url: Optional[str] = Field(
        None, description="URL of a screenshot of the page", title="Screenshot URL"
    )
    first_seen: Optional[str] = Field(
        None, description="First time the page was observed", title="First Seen"
    )
    last_seen: Optional[str] = Field(
        None, description="Last time the page was observed", title="Last Seen"
    )
    takedown_status: Optional[str] = Field(
        None, description="Takedown progress", title="Takedown Status"
    )
    source: Optional[str] = Field(
        None, description="Where the page was reported from", title="Source"
    )

    @field_validator("url")
    @classmethod
    def strip_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("url must not be empty")
        return v

    @model_validator(mode="after")
    def compute_label(self) -> Self:
        self.nodeLabel = self.url
        return self

    @classmethod
    def from_string(cls, line: str):
        """Parse a phishing page from a raw URL string."""
        return cls(url=line.strip())

    @classmethod
    def detect(cls, line: str) -> bool:
        """A URL alone does not say it is phishing; Website handles plain URLs."""
        return False
