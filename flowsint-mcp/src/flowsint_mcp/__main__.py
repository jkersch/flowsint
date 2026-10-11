"""Entry point: `flowsint-mcp` or `python -m flowsint_mcp`."""

import sys

import uvicorn

from .config import ConfigError, Settings
from .server import create_http_app, create_server


def main() -> int:
    try:
        settings = Settings.from_env()
    except ConfigError as e:
        print(f"flowsint-mcp: {e}", file=sys.stderr)
        return 2
    if settings.transport == "http":
        uvicorn.run(create_http_app(settings), host=settings.host, port=settings.port)
    else:
        create_server(settings).run("stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
