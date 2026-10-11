import re
from typing import List, Optional, Self

from pydantic import Field, field_validator, model_validator

from .flowsint_base import FlowsintType
from .registry import flowsint_type


@flowsint_type
class Phishkit(FlowsintType):
    """Represents a phishing kit archive, identified by its SHA-256 checksum."""

    sha256: str = Field(
        ...,
        description="SHA-256 checksum of the kit archive",
        title="SHA-256",
        json_schema_extra={"primary": True},
    )
    file_name: Optional[str] = Field(
        None, description="Original archive file name", title="File Name"
    )
    file_size: Optional[int] = Field(
        None, description="Archive size in bytes", title="File Size", ge=0
    )
    source_url: Optional[str] = Field(
        None, description="URL the kit was downloaded from", title="Source URL"
    )
    family: Optional[str] = Field(
        None, description="Kit family or author signature", title="Family"
    )
    target_brands: Optional[List[str]] = Field(
        None, description="Brands the kit impersonates", title="Target Brands"
    )
    telegram_bot_tokens: Optional[List[str]] = Field(
        None,
        description="Telegram bot tokens used for exfiltration",
        title="Telegram Bot Tokens",
    )
    exfil_emails: Optional[List[str]] = Field(
        None,
        description="Email addresses receiving stolen data",
        title="Exfiltration Emails",
    )
    first_seen: Optional[str] = Field(
        None, description="First time the kit was observed", title="First Seen"
    )
    last_seen: Optional[str] = Field(
        None, description="Last time the kit was observed", title="Last Seen"
    )
    description: Optional[str] = Field(
        None, description="Analyst notes", title="Description"
    )
    source: Optional[str] = Field(
        None, description="Source of the kit information", title="Source"
    )

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError(f"Invalid SHA-256 checksum: {v}")
        return v

    @model_validator(mode="after")
    def compute_label(self) -> Self:
        # Derived from the primary key only: nodes are merged on nodeLabel, so a
        # label that changes with optional fields would split one kit into two nodes.
        self.nodeLabel = f"kit:{self.sha256[:12]}"
        return self

    @classmethod
    def from_string(cls, line: str):
        """Parse a phishkit from a raw SHA-256 string."""
        return cls(sha256=line.strip())

    @classmethod
    def detect(cls, line: str) -> bool:
        """A bare SHA-256 could be any file; don't auto-detect it as a kit."""
        return False
