"""Tests for the type picker categories served by TypeRegistryService.

A category child names a type and the field used as its label. Both are plain
strings, so a typo or a renamed field silently drops the type from the UI
(get_types_list only prints a warning) instead of failing anywhere.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from flowsint_core.core.services.type_registry_service import TypeRegistryService
from flowsint_types.registry import get_type


@pytest.fixture
def service():
    custom_type_repo = MagicMock()
    custom_type_repo.get_by_owner.return_value = []
    return TypeRegistryService(db=MagicMock(), custom_type_repo=custom_type_repo)


def _children(service):
    for category in service._get_category_definitions():
        for type_name, label_key, icon in category["children"]:
            yield category["type"], type_name, label_key, icon


def test_every_category_child_is_a_registered_type(service):
    missing = [
        name
        for _, name, _, _ in _children(service)
        if get_type(name, case_sensitive=True) is None
    ]
    assert missing == []


def test_every_label_key_is_a_field_of_its_type(service):
    wrong = [
        (name, label_key)
        for _, name, label_key, _ in _children(service)
        if label_key not in get_type(name, case_sensitive=True).model_fields
    ]
    assert wrong == []


@pytest.mark.parametrize(
    "category, type_name, label_key",
    [
        ("threat_intel_category", "PhishingPage", "url"),
        ("threat_intel_category", "Phishkit", "sha256"),
        ("threat_intel_category", "ThreatActor", "name"),
        ("network_category", "Registrar", "name"),
        ("network_category", "ISP", "name"),
    ],
)
def test_threat_intel_types_are_offered(service, category, type_name, label_key):
    assert (category, type_name, label_key) in {
        (c, n, k) for c, n, k, _ in _children(service)
    }


def test_types_list_exposes_threat_intel_category(service):
    types = service.get_types_list(uuid4())
    category = next(t for t in types if t["type"] == "threat_intel_category")

    children = {child["type"]: child for child in category["children"]}
    assert set(children) == {"PhishingPage", "Phishkit", "ThreatActor"}
    assert children["Phishkit"]["label_key"] == "sha256"
    assert children["Phishkit"]["icon"] == "phishkit"
    field_names = {f["name"] for f in children["Phishkit"]["fields"]}
    assert {"sha256", "file_name", "telegram_bot_tokens"} <= field_names
    assert "nodeLabel" not in field_names


def test_types_list_drops_no_category_child(service):
    """A child that fails to resolve is skipped, so counts must match."""
    types = service.get_types_list(uuid4())
    served = {t["type"]: len(t["children"]) for t in types}
    defined = {
        c["type"]: len(c["children"]) for c in service._get_category_definitions()
    }
    assert served == defined
