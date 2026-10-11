import pytest

from flowsint_mcp.config import ConfigError, Settings


def env(**values):
    return {f"FLOWSINT_{k}": v for k, v in values.items()}


def test_defaults_with_token():
    s = Settings.from_env(env(API_TOKEN="tok"))
    assert s == Settings(api_token="tok")
    assert s.api_url == "http://localhost:5001"
    assert s.transport == "stdio"
    assert s.read_only is False
    assert s.enrich_timeout == 120.0
    assert s.can_login is False


def test_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("FLOWSINT_API_TOKEN", "from-env")
    assert Settings.from_env().api_token == "from-env"


def test_overrides_and_normalization():
    s = Settings.from_env(
        env(
            API_URL=" https://flowsint.example/ ",
            API_TOKEN=" tok ",
            MCP_TRANSPORT="STDIO",
            MCP_READ_ONLY="Yes",
            MCP_ENRICH_TIMEOUT="30.5",
        )
    )
    assert s.api_url == "https://flowsint.example"
    assert s.api_token == "tok"
    assert s.read_only is True
    assert s.enrich_timeout == 30.5


def test_email_and_password_instead_of_token():
    s = Settings.from_env(env(EMAIL="me@x", PASSWORD="pw"))
    assert s.api_token is None
    assert s.can_login is True


def test_token_and_login_may_both_be_set():
    s = Settings.from_env(env(API_TOKEN="tok", EMAIL="me@x", PASSWORD="pw"))
    assert s.api_token == "tok" and s.can_login


@pytest.mark.parametrize(
    "values",
    [{}, {"EMAIL": "me@x"}, {"PASSWORD": "pw"}, {"API_TOKEN": "   "}],
)
def test_credentials_are_required(values):
    with pytest.raises(ConfigError, match="FLOWSINT_API_TOKEN"):
        Settings.from_env(env(**values))


@pytest.mark.parametrize(
    "values, message",
    [
        ({"API_URL": "localhost:5001"}, "must start with http"),
        ({"MCP_TRANSPORT": "grpc"}, "must be one of stdio, http"),
        ({"MCP_PORT": "0"}, "must be a port number"),
        ({"MCP_PORT": "70000"}, "must be a port number"),
        ({"MCP_PORT": "http"}, "must be a port number"),
        ({"MCP_READ_ONLY": "maybe"}, "true or false"),
        ({"MCP_ENRICH_TIMEOUT": "0"}, "positive number"),
        ({"MCP_ENRICH_TIMEOUT": "-5"}, "positive number"),
        ({"MCP_ENRICH_TIMEOUT": "soon"}, "positive number"),
    ],
)
def test_invalid_values(values, message):
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(env(API_TOKEN="tok", **values))


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE"])
def test_read_only_false_values(value):
    assert Settings.from_env(env(API_TOKEN="t", MCP_READ_ONLY=value)).read_only is False


def test_http_mode_needs_no_credentials():
    s = Settings.from_env(
        env(
            MCP_TRANSPORT="http",
            MCP_HOST="127.0.0.1",
            MCP_PORT="8080",
            MCP_ALLOWED_HOSTS="mcp.example.com, localhost:8080 ,",
            MCP_ALLOWED_ORIGINS="https://claude.ai",
        )
    )
    assert s.transport == "http" and s.passthrough
    assert (s.host, s.port) == ("127.0.0.1", 8080)
    assert s.allowed_hosts == ["mcp.example.com", "localhost:8080"]
    assert s.allowed_origins == ["https://claude.ai"]


def test_http_defaults():
    s = Settings.from_env(env(MCP_TRANSPORT="HTTP"))
    assert (s.host, s.port, s.allowed_hosts, s.allowed_origins) == (
        "0.0.0.0",
        5002,
        [],
        [],
    )


@pytest.mark.parametrize(
    "credential", [{"API_TOKEN": "t"}, {"EMAIL": "e"}, {"PASSWORD": "p"}]
)
def test_http_mode_refuses_server_credentials(credential):
    with pytest.raises(ConfigError, match="must not be set"):
        Settings.from_env(env(MCP_TRANSPORT="http", **credential))


def test_http_mode_never_logs_in():
    s = Settings(transport="http", email="e", password="p")
    assert s.can_login is False


def test_stdio_is_not_passthrough():
    assert Settings(api_token="t").passthrough is False
