"""Tests for SketchService.search_nodes and SketchService.get_stats."""

from uuid import uuid4

import pytest

from flowsint_core.core.graph import GraphSerializer, GraphService
from flowsint_core.core.services import sketch_service as sketch_service_module
from flowsint_core.core.services.exceptions import (
    DatabaseError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from flowsint_core.core.services.sketch_service import (
    SEARCH_MAX_LIMIT,
    create_sketch_service,
)
from flowsint_core.core.types import Role
from flowsint_types import Domain, PhishingPage
from tests.core.graph.in_memory_graph_repository import InMemoryGraphRepository
from tests.factories import (
    InvestigationFactory,
    InvestigationUserRoleFactory,
    ProfileFactory,
    SketchFactory,
)


def _setup(db_session):
    for f in (
        ProfileFactory,
        InvestigationFactory,
        InvestigationUserRoleFactory,
        SketchFactory,
    ):
        f._meta.sqlalchemy_session = db_session


@pytest.fixture
def graph(monkeypatch):
    """Route the service's graph access to one shared in-memory repository."""
    repo = InMemoryGraphRepository()
    created = []

    def fake_create_graph_service(sketch_id, type_resolver=None, **kwargs):
        service = GraphService(
            sketch_id=sketch_id, repository=repo, type_resolver=type_resolver
        )
        created.append(service)
        return service

    monkeypatch.setattr(
        sketch_service_module, "create_graph_service", fake_create_graph_service
    )
    repo.created_services = created
    return repo


def make_sketch(db_session, role=Role.OWNER):
    _setup(db_session)
    sketch = SketchFactory()
    user = ProfileFactory()
    if role is not None:
        InvestigationUserRoleFactory(
            user=user, investigation=sketch.investigation, roles=[role]
        )
    return sketch, user


def add(repo, sketch, *entities):
    repo.batch_create_nodes(
        GraphSerializer.serialize_flowsint_types(list(entities)), str(sketch.id)
    )


class TestSearchNodesPermissions:
    @pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN, Role.EDITOR, Role.VIEWER])
    def test_every_role_can_search(self, db_session, graph, role):
        sketch, user = make_sketch(db_session, role)
        add(graph, sketch, Domain(domain="a.example"))
        result = create_sketch_service(db_session).search_nodes(sketch.id, user.id)
        assert [n.nodeLabel for n in result["nodes"]] == ["a.example"]

    def test_user_without_role_is_denied(self, db_session, graph):
        sketch, user = make_sketch(db_session, role=None)
        with pytest.raises(PermissionDeniedError):
            create_sketch_service(db_session).search_nodes(sketch.id, user.id)

    def test_unknown_sketch(self, db_session, graph):
        _setup(db_session)
        user = ProfileFactory()
        with pytest.raises(NotFoundError):
            create_sketch_service(db_session).search_nodes(uuid4(), user.id)


class TestSearchNodesBehaviour:
    @pytest.fixture
    def setup(self, db_session, graph):
        sketch, user = make_sketch(db_session)
        add(
            graph,
            sketch,
            Domain(domain="rbfcu.example"),
            PhishingPage(url="https://rbfcu.example/login", status="VERIFIED"),
            PhishingPage(url="https://x.example/", status="PENDING"),
        )
        return create_sketch_service(db_session), sketch, user

    def test_types_are_case_insensitive(self, setup):
        service, sketch, user = setup
        result = service.search_nodes(sketch.id, user.id, types=["PhishingPage"])
        assert len(result["nodes"]) == 2

    def test_query_is_stripped_and_blank_query_matches_all(self, setup):
        service, sketch, user = setup
        hits = service.search_nodes(sketch.id, user.id, query="  rbfcu ")["nodes"]
        assert len(hits) == 2
        assert len(service.search_nodes(sketch.id, user.id, query="   ")["nodes"]) == 3

    def test_filters_and_paging(self, setup):
        service, sketch, user = setup
        page = service.search_nodes(
            sketch.id, user.id, filters={"status": "VERIFIED"}, limit=1
        )
        assert [n.nodeLabel for n in page["nodes"]] == ["https://rbfcu.example/login"]
        assert page["next_cursor"] is None

        first = service.search_nodes(sketch.id, user.id, limit=2)
        rest = service.search_nodes(
            sketch.id, user.id, limit=2, cursor=first["next_cursor"]
        )
        assert len(first["nodes"]) == 2 and len(rest["nodes"]) == 1

    def test_uses_the_users_type_resolver(self, setup, graph):
        service, sketch, user = setup
        service.search_nodes(sketch.id, user.id)
        assert graph.created_services[-1]._type_resolver is not None

    @pytest.mark.parametrize("limit", [0, -1, SEARCH_MAX_LIMIT + 1])
    def test_limit_out_of_range(self, setup, limit):
        service, sketch, user = setup
        with pytest.raises(ValidationError, match="limit"):
            service.search_nodes(sketch.id, user.id, limit=limit)

    def test_unknown_types_are_listed(self, setup):
        service, sketch, user = setup
        with pytest.raises(ValidationError, match="Unknown type\\(s\\): Nope, Zilch"):
            service.search_nodes(sketch.id, user.id, types=["Domain", "Nope", "Zilch"])

    @pytest.mark.parametrize("value", [["a"], {"a": 1}, None])
    def test_non_scalar_filter_values_are_rejected(self, setup, value):
        service, sketch, user = setup
        with pytest.raises(ValidationError, match="must be a string, number"):
            service.search_nodes(sketch.id, user.id, filters={"status": value})

    def test_invalid_filter_field_becomes_validation_error(self, setup):
        service, sketch, user = setup
        with pytest.raises(ValidationError, match="Invalid filter field"):
            service.search_nodes(sketch.id, user.id, filters={"a b": "x"})

    def test_backend_failure_becomes_database_error(self, setup, graph, monkeypatch):
        service, sketch, user = setup

        def boom(**kwargs):
            raise RuntimeError("neo4j down")

        monkeypatch.setattr(graph, "search_nodes", boom)
        with pytest.raises(DatabaseError, match="Failed to search nodes"):
            service.search_nodes(sketch.id, user.id)

    def test_without_type_registry_falls_back_to_local_types(self, db_session, graph):
        sketch, user = make_sketch(db_session)
        add(graph, sketch, Domain(domain="a.example"))
        service = create_sketch_service(db_session)
        service._type_registry = None
        result = service.search_nodes(sketch.id, user.id, types=["domain"])
        assert len(result["nodes"]) == 1
        with pytest.raises(ValidationError):
            service.search_nodes(sketch.id, user.id, types=["NotAType"])


class TestGetStats:
    def test_returns_graph_stats(self, db_session, graph):
        sketch, user = make_sketch(db_session, Role.VIEWER)
        add(graph, sketch, Domain(domain="a.example"), Domain(domain="b.example"))
        stats = create_sketch_service(db_session).get_stats(sketch.id, user.id)
        assert stats["nodes"] == 2
        assert stats["by_type"] == {"domain": 2}

    def test_user_without_role_is_denied(self, db_session, graph):
        sketch, user = make_sketch(db_session, role=None)
        with pytest.raises(PermissionDeniedError):
            create_sketch_service(db_session).get_stats(sketch.id, user.id)

    def test_unknown_sketch(self, db_session, graph):
        _setup(db_session)
        with pytest.raises(NotFoundError):
            create_sketch_service(db_session).get_stats(uuid4(), ProfileFactory().id)

    def test_backend_failure_becomes_database_error(
        self, db_session, graph, monkeypatch
    ):
        sketch, user = make_sketch(db_session)

        def boom(*args, **kwargs):
            raise RuntimeError("neo4j down")

        monkeypatch.setattr(graph, "get_sketch_stats", boom)
        with pytest.raises(DatabaseError, match="statistics"):
            create_sketch_service(db_session).get_stats(sketch.id, user.id)


class TestGetForUpdate:
    @pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN, Role.EDITOR])
    def test_writers_are_allowed(self, db_session, role):
        sketch, user = make_sketch(db_session, role)
        got = create_sketch_service(db_session).get_for_update(sketch.id, user.id)
        assert got.id == sketch.id

    @pytest.mark.parametrize("role", [Role.VIEWER, None])
    def test_viewers_and_strangers_are_denied(self, db_session, role):
        sketch, user = make_sketch(db_session, role)
        with pytest.raises(PermissionDeniedError):
            create_sketch_service(db_session).get_for_update(sketch.id, user.id)

    def test_unknown_sketch(self, db_session):
        _setup(db_session)
        with pytest.raises(NotFoundError):
            create_sketch_service(db_session).get_for_update(
                uuid4(), ProfileFactory().id
            )


class TestSearchInvestigation:
    @pytest.fixture
    def setup(self, db_session, graph):
        _setup(db_session)
        investigation = InvestigationFactory()
        infra = SketchFactory(investigation=investigation, title="Infra")
        kits = SketchFactory(investigation=investigation, title="Kits")
        elsewhere = SketchFactory(title="Other case")
        add(graph, infra, Domain(domain="evil.example"))
        add(graph, kits, Domain(domain="evil.example"), Domain(domain="b.example"))
        add(graph, elsewhere, Domain(domain="evil.example"))
        return investigation, infra, kits

    def member(self, investigation, role=Role.VIEWER):
        user = ProfileFactory()
        if role:
            InvestigationUserRoleFactory(
                user=user, investigation=investigation, roles=[role]
            )
        return user

    def test_hits_from_every_sketch_with_titles(self, db_session, setup):
        investigation, infra, kits = setup
        result = create_sketch_service(db_session).search_investigation(
            investigation.id, self.member(investigation).id, query="evil"
        )
        assert result["next_cursor"] is None
        assert sorted(
            (h["sketch_title"], h["sketch_id"], h["node"].nodeLabel)
            for h in result["nodes"]
        ) == sorted(
            [
                ("Infra", str(infra.id), "evil.example"),
                ("Kits", str(kits.id), "evil.example"),
            ]
        )

    def test_validates_like_sketch_search(self, db_session, setup):
        investigation, _, _ = setup
        service = create_sketch_service(db_session)
        user = self.member(investigation)
        with pytest.raises(ValidationError, match="Unknown type"):
            service.search_investigation(investigation.id, user.id, types=["Nope"])
        with pytest.raises(ValidationError, match="limit"):
            service.search_investigation(investigation.id, user.id, limit=0)

    def test_stranger_is_denied(self, db_session, setup):
        investigation, _, _ = setup
        with pytest.raises(PermissionDeniedError):
            create_sketch_service(db_session).search_investigation(
                investigation.id, self.member(investigation, role=None).id
            )

    def test_unknown_investigation(self, db_session, graph):
        _setup(db_session)
        with pytest.raises(NotFoundError, match="Investigation not found"):
            create_sketch_service(db_session).search_investigation(
                uuid4(), ProfileFactory().id
            )

    def test_investigation_without_sketches(self, db_session, graph):
        _setup(db_session)
        investigation = InvestigationFactory()
        user = self.member(investigation)
        assert create_sketch_service(db_session).search_investigation(
            investigation.id, user.id
        ) == {"nodes": [], "next_cursor": None}

    def test_invalid_filter_field(self, db_session, setup):
        investigation, _, _ = setup
        with pytest.raises(ValidationError, match="Invalid filter field"):
            create_sketch_service(db_session).search_investigation(
                investigation.id, self.member(investigation).id, filters={"a b": 1}
            )

    def test_backend_failure(self, db_session, setup, graph, monkeypatch):
        investigation, _, _ = setup

        def boom(**kwargs):
            raise RuntimeError("down")

        monkeypatch.setattr(graph, "search_nodes", boom)
        with pytest.raises(DatabaseError):
            create_sketch_service(db_session).search_investigation(
                investigation.id, self.member(investigation).id
            )
