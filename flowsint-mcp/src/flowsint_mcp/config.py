"""Server settings, read from environment variables."""

import os
from dataclasses import dataclass, field
from typing import List, Mapping, Optional

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
TRANSPORTS = ("stdio", "http")


class ConfigError(ValueError):
    """A setting is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    api_url: str = "http://localhost:5001"
    api_token: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    transport: str = "stdio"
    read_only: bool = False
    enrich_timeout: float = 120.0
    host: str = "0.0.0.0"
    port: int = 5002
    allowed_hosts: List[str] = field(default_factory=list)
    allowed_origins: List[str] = field(default_factory=list)

    @property
    def can_login(self) -> bool:
        # Never in HTTP mode, even if a Settings is built by hand with both.
        return bool(self.email and self.password) and not self.passthrough

    @property
    def passthrough(self) -> bool:
        """HTTP mode: every MCP request brings its own Flowsint token."""
        return self.transport == "http"

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if env is None else env

        def get(name: str) -> Optional[str]:
            value = env.get(name)
            return value.strip() if value and value.strip() else None

        api_url = (get("FLOWSINT_API_URL") or cls.api_url).rstrip("/")
        if not api_url.startswith(("http://", "https://")):
            raise ConfigError(
                f"FLOWSINT_API_URL must start with http:// or https://, got {api_url!r}"
            )

        transport = (get("FLOWSINT_MCP_TRANSPORT") or "stdio").lower()
        if transport not in TRANSPORTS:
            raise ConfigError(
                f"FLOWSINT_MCP_TRANSPORT must be one of {', '.join(TRANSPORTS)}, "
                f"got {transport!r}"
            )

        token, email, password = (
            get("FLOWSINT_API_TOKEN"),
            get("FLOWSINT_EMAIL"),
            get("FLOWSINT_PASSWORD"),
        )
        if transport == "http":
            # A shared server must never fall back to one user's rights for
            # everybody: each client sends its own token instead.
            if token or email or password:
                raise ConfigError(
                    "FLOWSINT_API_TOKEN, FLOWSINT_EMAIL and FLOWSINT_PASSWORD must "
                    "not be set with FLOWSINT_MCP_TRANSPORT=http; clients send "
                    "their own token in the Authorization header"
                )
        elif not token and not (email and password):
            raise ConfigError(
                "Set FLOWSINT_API_TOKEN, or FLOWSINT_EMAIL and FLOWSINT_PASSWORD"
            )

        raw_port = get("FLOWSINT_MCP_PORT")
        try:
            port = int(raw_port) if raw_port else cls.port
        except ValueError:
            port = 0
        if not 1 <= port <= 65535:
            raise ConfigError(
                f"FLOWSINT_MCP_PORT must be a port number, got {raw_port!r}"
            )

        read_only = (get("FLOWSINT_MCP_READ_ONLY") or "false").lower()
        if read_only not in TRUE_VALUES | FALSE_VALUES:
            raise ConfigError(
                f"FLOWSINT_MCP_READ_ONLY must be true or false, got {read_only!r}"
            )

        raw_timeout = get("FLOWSINT_MCP_ENRICH_TIMEOUT")
        try:
            enrich_timeout = float(raw_timeout) if raw_timeout else cls.enrich_timeout
        except ValueError:
            enrich_timeout = -1
        if enrich_timeout <= 0:
            raise ConfigError(
                "FLOWSINT_MCP_ENRICH_TIMEOUT must be a positive number of seconds, "
                f"got {raw_timeout!r}"
            )

        return cls(
            api_url=api_url,
            api_token=token,
            email=email,
            password=password,
            transport=transport,
            read_only=read_only in TRUE_VALUES,
            enrich_timeout=enrich_timeout,
            host=get("FLOWSINT_MCP_HOST") or cls.host,
            port=port,
            allowed_hosts=_split(get("FLOWSINT_MCP_ALLOWED_HOSTS")),
            allowed_origins=_split(get("FLOWSINT_MCP_ALLOWED_ORIGINS")),
        )


def _split(value: Optional[str]) -> List[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]
