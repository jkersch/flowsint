import pytest
from pydantic import ValidationError

from flowsint_types import (
    ISP,
    TYPE_TO_MODEL,
    PhishingPage,
    Phishkit,
    Registrar,
    ThreatActor,
    get_type,
)

SHA = "7b4791d7a3ad845d36e7d927abc32d0d2a85f67e380213197db000e7c6cb2509"


@pytest.mark.parametrize(
    "name", ["PhishingPage", "Phishkit", "ThreatActor", "Registrar", "ISP"]
)
def test_registered(name):
    assert get_type(name, case_sensitive=True) is not None
    assert TYPE_TO_MODEL[name.lower()].__name__ == name


def test_phishing_page_label_is_url():
    page = PhishingPage(url="  https://evil.example/login  ", status="VERIFIED")
    assert page.url == "https://evil.example/login"
    assert page.nodeLabel == "https://evil.example/login"


def test_phishing_page_accepts_urls_httpurl_would_reject():
    """Phishing URLs are often malformed; the primary key must not be HttpUrl."""
    page = PhishingPage(url="http://xn--pypal-4ve.com:8080/%%/login")
    assert page.nodeLabel == "http://xn--pypal-4ve.com:8080/%%/login"


def test_phishing_page_rejects_empty_url():
    with pytest.raises(ValidationError):
        PhishingPage(url="   ")


def test_phishing_page_not_auto_detected():
    assert PhishingPage.detect("https://evil.example/login") is False


def test_phishkit_normalizes_sha256():
    kit = Phishkit(sha256=SHA.upper())
    assert kit.sha256 == SHA


def test_phishkit_rejects_non_sha256():
    with pytest.raises(ValidationError, match="Invalid SHA-256"):
        Phishkit(sha256="d41d8cd98f00b204e9800998ecf8427e")


def test_phishkit_label_depends_only_on_hash():
    """Nodes merge on nodeLabel, so optional fields must not change it."""
    bare = Phishkit(sha256=SHA)
    full = Phishkit(sha256=SHA, file_name="RBFCU j.zip", family="x")
    assert bare.nodeLabel == full.nodeLabel == f"kit:{SHA[:12]}"


def test_phishkit_lists_round_trip():
    kit = Phishkit(sha256=SHA, target_brands=["RBFCU"], telegram_bot_tokens=[])
    assert kit.target_brands == ["RBFCU"]
    assert kit.telegram_bot_tokens == []


def test_phishkit_file_size_non_negative():
    with pytest.raises(ValidationError):
        Phishkit(sha256=SHA, file_size=-1)


@pytest.mark.parametrize("cls", [ThreatActor, Registrar, ISP])
def test_name_types_label_and_from_string(cls):
    obj = cls.from_string("  Acme  ")
    assert obj.name == "Acme"
    assert obj.nodeLabel == "Acme"
    assert cls.detect("Acme") is False


def test_extra_fields_allowed():
    actor = ThreatActor(name="Group-X", hub_id=42)
    assert actor.model_dump()["hub_id"] == 42


def test_phishing_page_from_string():
    assert PhishingPage.from_string(" https://evil.example/ \n").url == (
        "https://evil.example/"
    )


def test_phishkit_from_string_and_detect():
    assert Phishkit.from_string(f"  {SHA.upper()}  ").sha256 == SHA
    assert Phishkit.detect(SHA) is False
