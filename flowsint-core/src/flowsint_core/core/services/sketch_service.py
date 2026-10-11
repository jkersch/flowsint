"""
Sketch service for managing sketches and graph operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union
from uuid import UUID

from sqlalchemy.orm import Session

from ..graph import GraphNode, create_graph_service
from ..models import Sketch
from ..repositories import InvestigationRepository, SketchRepository
from .base import BaseService
from .exceptions import (
    DatabaseError,
    NotFoundError,
    ValidationError,
)

if TYPE_CHECKING:
    from ..graph.serializer import TypeResolver
    from .type_registry_service import TypeRegistryService


SEARCH_MAX_LIMIT = 500


class SketchService(BaseService):
    """
    Service for sketch CRUD operations and graph interactions.
    """

    def __init__(
        self,
        db: Session,
        sketch_repo: SketchRepository,
        investigation_repo: InvestigationRepository,
        type_registry_service: Optional[TypeRegistryService] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(db, **kwargs)
        self._sketch_repo = sketch_repo
        self._investigation_repo = investigation_repo
        self._type_registry = type_registry_service

    def _get_sketch_with_permission(
        self, sketch_id: UUID, user_id: UUID, actions: List[str]
    ) -> Sketch:
        """Get sketch and verify user has permission."""
        sketch = self._sketch_repo.get_by_id(sketch_id)
        if not sketch:
            raise NotFoundError("Sketch not found")
        self._check_permission(user_id, sketch.investigation_id, actions)
        return sketch

    def list_sketches(self, user_id: UUID) -> List[Sketch]:
        """Get all sketches owned by a user."""
        return self._sketch_repo.get_by_owner(user_id)

    def get_by_id(self, sketch_id: UUID, user_id: UUID) -> Sketch:
        """Get a sketch by ID with permission check."""
        return self._get_sketch_with_permission(sketch_id, user_id, ["read"])

    def get_for_update(self, sketch_id: UUID, user_id: UUID) -> Sketch:
        """Get a sketch the user may write to (add, edit or import nodes)."""
        return self._get_sketch_with_permission(sketch_id, user_id, ["update"])

    def create(
        self,
        title: str,
        description: Optional[str],
        investigation_id: UUID,
        owner_id: UUID,
    ) -> Sketch:
        if not investigation_id:
            raise ValidationError("Investigation not found")

        self._check_permission(owner_id, investigation_id, ["create"])

        sketch = Sketch(
            title=title,
            description=description,
            investigation_id=investigation_id,
            owner_id=owner_id,
        )
        self._sketch_repo.add(sketch)
        self._commit()
        self._refresh(sketch)
        return sketch

    def update(self, sketch_id: UUID, user_id: UUID, updates: Dict[str, Any]) -> Sketch:
        sketch = self._get_sketch_with_permission(sketch_id, user_id, ["update"])

        for key, value in updates.items():
            if hasattr(sketch, key):
                setattr(sketch, key, value)

        self._commit()
        self._refresh(sketch)
        return sketch

    def delete(self, sketch_id: UUID, user_id: UUID) -> None:
        sketch = self._get_sketch_with_permission(sketch_id, user_id, ["delete"])

        try:
            graph_service = create_graph_service(
                sketch_id=str(sketch_id), enable_batching=False
            )
            graph_service.delete_all_sketch_nodes()
        except Exception as e:
            print(f"Neo4j cleanup error: {e}")
            raise DatabaseError("Failed to clean up graph data")

        self._sketch_repo.delete(sketch)
        self._commit()

    # --- Graph operations ---

    # format="inline" returns a flat list of {source, edge, target} dicts
    # instead of the default {nds, rls}-shaped graph — same distinction the
    # frontend's sketchService.getGraphDataById overload encodes.
    def get_graph(
        self, sketch_id: UUID, user_id: UUID, format: Optional[str] = None
    ) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        self._get_sketch_with_permission(sketch_id, user_id, ["read"])

        resolver = (
            self._type_registry.build_type_resolver(user_id)
            if self._type_registry
            else None
        )
        graph_service = create_graph_service(
            sketch_id=str(sketch_id),
            enable_batching=False,
            type_resolver=resolver,
        )
        graph_data = graph_service.get_sketch_graph()

        if format == "inline":
            from flowsint_core.utils import get_inline_relationships

            return get_inline_relationships(graph_data.nodes, graph_data.edges)

        graph = graph_data.model_dump(mode="json", serialize_as_any=True)
        return {"nds": graph["nodes"], "rls": graph["edges"]}

    def add_node(
        self, sketch_id: UUID, user_id: UUID, node: GraphNode
    ) -> Dict[str, Any]:
        self._get_sketch_with_permission(sketch_id, user_id, ["update"])

        try:
            graph_service = create_graph_service(
                sketch_id=str(sketch_id), enable_batching=False
            )
            node_id = graph_service.create_node(node)
        except Exception as e:
            print(e)
            raise DatabaseError(f"Database error: {str(e)}")

        if not node_id:
            raise ValidationError("Node creation failed")

        node.id = node_id
        return {"status": "node added", "node": node}

    def add_relationship(
        self,
        sketch_id: UUID,
        user_id: UUID,
        source: str,
        target: str,
        label: str = "RELATED_TO",
    ) -> Dict[str, Any]:
        self._get_sketch_with_permission(sketch_id, user_id, ["update"])

        try:
            graph_service = create_graph_service(
                sketch_id=str(sketch_id), enable_batching=False
            )
            result = graph_service.create_relationship_by_element_id(
                from_element_id=source,
                to_element_id=target,
                rel_label=label,
            )
        except Exception as e:
            print(f"Edge creation error: {e}")
            raise DatabaseError("Failed to create edge")

        if not result:
            raise ValidationError("Edge creation failed")

        return {"status": "edge added", "edge": result}

    def update_node(
        self, sketch_id: UUID, user_id: UUID, node_id: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        self._get_sketch_with_permission(sketch_id, user_id, ["update"])

        try:
            graph_service = create_graph_service(
                sketch_id=str(sketch_id), enable_batching=False
            )
            updated_element_id = graph_service.update_node(
                element_id=node_id, updates=updates
            )
        except Exception as e:
            print(f"Node update error: {e}")
            raise DatabaseError("Failed to update node")

        if not updated_element_id:
            raise NotFoundError("Node not found or not accessible")

        return {"status": "node updated", "node": {"id": updated_element_id}}

    def update_node_positions(
        self, sketch_id: UUID, user_id: UUID, positions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        self._get_sketch_with_permission(sketch_id, user_id, ["update"])

        if not positions:
            return {"status": "no positions to update", "count": 0}

        try:
            graph_service = create_graph_service(
                sketch_id=str(sketch_id), enable_batching=False
            )
            updated_count = graph_service.update_nodes_positions(positions=positions)
        except Exception as e:
            print(f"Position update error: {e}")
            raise DatabaseError("Failed to update node positions")

        return {"status": "positions updated", "count": updated_count}

    def delete_nodes(
        self, sketch_id: UUID, user_id: UUID, node_ids: List[str]
    ) -> Dict[str, Any]:
        self._get_sketch_with_permission(sketch_id, user_id, ["update"])

        try:
            graph_service = create_graph_service(
                sketch_id=str(sketch_id), enable_batching=False
            )
            deleted_count = graph_service.delete_nodes(node_ids)
        except Exception as e:
            print(f"Node deletion error: {e}")
            raise DatabaseError("Failed to delete nodes")

        return {"status": "nodes deleted", "count": deleted_count}

    def delete_relationships(
        self, sketch_id: UUID, user_id: UUID, relationship_ids: List[str]
    ) -> Dict[str, Any]:
        self._get_sketch_with_permission(sketch_id, user_id, ["update"])
        try:
            graph_service = create_graph_service(
                sketch_id=str(sketch_id), enable_batching=False
            )
            deleted_count = graph_service.delete_relationships(relationship_ids)
        except Exception as e:
            print(f"Relationship deletion error: {e}")
            raise DatabaseError("Failed to delete relationships")

        return {"status": "relationships deleted", "count": deleted_count}

    def update_relationship(
        self,
        sketch_id: UUID,
        user_id: UUID,
        relationship_id: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        self._get_sketch_with_permission(sketch_id, user_id, ["update"])

        try:
            graph_service = create_graph_service(
                sketch_id=str(sketch_id), enable_batching=False
            )
            result = graph_service.update_relationship(
                element_id=relationship_id, properties=data
            )
        except Exception as e:
            print(f"Relationship update error: {e}")
            raise DatabaseError("Failed to update relationship")

        if not result:
            raise NotFoundError("Relationship not found or not accessible")

        return {
            "status": "relationship updated",
            "relationship": {
                "id": result["id"],
                "label": result["type"],
                "data": result["data"],
            },
        }

    def merge_nodes(
        self,
        sketch_id: UUID,
        user_id: UUID,
        old_node_ids: List[str],
        new_node_id: str,
        node_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        from flowsint_core.utils import flatten

        self._get_sketch_with_permission(sketch_id, user_id, ["update"])

        if not old_node_ids:
            raise ValidationError("oldNodes cannot be empty")

        node_type = node_data.get("type", "Node")
        properties = {
            "type": node_type.lower(),
            "label": node_data.get("label", "Merged Node"),
        }
        flattened_data = flatten(node_data)
        properties.update(flattened_data)

        try:
            graph_service = create_graph_service(
                sketch_id=str(sketch_id), enable_batching=False
            )
            new_node_element_id = graph_service.merge_nodes(
                old_node_ids=old_node_ids,
                new_node_data=properties,
                new_node_id=new_node_id,
            )
        except Exception as e:
            print(f"Node merge error: {e}")
            raise DatabaseError(f"Failed to merge nodes: {str(e)}")

        if not new_node_element_id:
            raise DatabaseError("Failed to merge nodes")

        return {
            "status": "nodes merged",
            "count": len(old_node_ids),
            "new_node_id": new_node_element_id,
        }

    def get_neighbors(
        self, sketch_id: UUID, user_id: UUID, node_id: str
    ) -> Dict[str, Any]:
        self._get_sketch_with_permission(sketch_id, user_id, ["read"])

        try:
            resolver = (
                self._type_registry.build_type_resolver(user_id)
                if self._type_registry
                else None
            )
            graph_service = create_graph_service(
                sketch_id=str(sketch_id),
                type_resolver=resolver,
            )
            result = graph_service.get_neighbors(node_id)
        except Exception as e:
            print(e)
            raise DatabaseError("Failed to retrieve related nodes")

        if not result.nodes:
            raise NotFoundError("Node not found")

        return {"nds": result.nodes, "rls": result.edges}

    def search_nodes(
        self,
        sketch_id: UUID,
        user_id: UUID,
        types: Optional[List[str]] = None,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Find nodes of a sketch by type, label substring and field values.

        Types are resolved like everywhere else (built-in and the user's custom
        types, case-insensitive); filters match FlowsintType fields exactly.
        """
        self._get_sketch_with_permission(sketch_id, user_id, ["read"])
        resolver, criteria = self._search_criteria(
            user_id, types, query, filters, limit
        )

        graph_service = create_graph_service(
            sketch_id=str(sketch_id), type_resolver=resolver
        )
        try:
            nodes, next_cursor = graph_service.search_nodes(**criteria, cursor=cursor)
        except ValueError as e:
            raise ValidationError(str(e))
        except Exception as e:
            print(e)
            raise DatabaseError("Failed to search nodes")
        return {"nodes": nodes, "next_cursor": next_cursor}

    def search_investigation(
        self,
        investigation_id: UUID,
        user_id: UUID,
        types: Optional[List[str]] = None,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """search_nodes across every sketch of an investigation.

        Each hit says which sketch it is in: {"sketch_id", "sketch_title", "node"}.
        """
        if not self._investigation_repo.get_by_id(investigation_id):
            raise NotFoundError("Investigation not found")
        self._check_permission(user_id, investigation_id, ["read"])
        resolver, criteria = self._search_criteria(
            user_id, types, query, filters, limit
        )

        sketches = self._sketch_repo.get_by_investigation(investigation_id)
        if not sketches:
            return {"nodes": [], "next_cursor": None}
        titles = {str(sketch.id): sketch.title for sketch in sketches}

        graph_service = create_graph_service(
            sketch_id=str(sketches[0].id), type_resolver=resolver
        )
        try:
            hits, next_cursor = graph_service.search_nodes_across(
                sketch_ids=list(titles), **criteria, cursor=cursor
            )
        except ValueError as e:
            raise ValidationError(str(e))
        except Exception as e:
            print(e)
            raise DatabaseError("Failed to search nodes")
        return {
            "nodes": [
                {"sketch_id": sid, "sketch_title": titles.get(sid), "node": node}
                for sid, node in hits
            ],
            "next_cursor": next_cursor,
        }

    def _search_criteria(
        self,
        user_id: UUID,
        types: Optional[List[str]],
        query: Optional[str],
        filters: Optional[Dict[str, Any]],
        limit: int,
    ) -> Tuple["TypeResolver", Dict[str, Any]]:
        """Validate search input; returns the user's resolver and clean criteria."""
        if not 1 <= limit <= SEARCH_MAX_LIMIT:
            raise ValidationError(f"limit must be between 1 and {SEARCH_MAX_LIMIT}")
        resolver = self._build_resolver(user_id)
        unknown = [t for t in types or [] if resolver(t) is None]
        if unknown:
            raise ValidationError(f"Unknown type(s): {', '.join(unknown)}")
        for field, value in (filters or {}).items():
            if not isinstance(value, (str, int, float, bool)):
                raise ValidationError(
                    f"Filter {field!r} must be a string, number or boolean"
                )
        query = query.strip() if query else None
        return resolver, {
            "types": [t.lower() for t in types] if types else None,
            "query": query or None,
            "filters": filters or None,
            "limit": limit,
        }

    def get_stats(self, sketch_id: UUID, user_id: UUID) -> Dict[str, Any]:
        """Node/edge counts by type and label, plus the most connected nodes."""
        self._get_sketch_with_permission(sketch_id, user_id, ["read"])
        graph_service = create_graph_service(sketch_id=str(sketch_id))
        try:
            return graph_service.get_stats()
        except Exception as e:
            print(e)
            raise DatabaseError("Failed to compute sketch statistics")

    def _build_resolver(self, user_id: UUID) -> TypeResolver:
        if self._type_registry:
            return self._type_registry.build_type_resolver(user_id)
        from .type_registry_service import local_type_resolver

        return local_type_resolver

    def export_sketch(
        self, sketch_id: UUID, user_id: UUID, format: str = "json"
    ) -> Dict[str, Any]:
        sketch = self._get_sketch_with_permission(sketch_id, user_id, ["read"])

        resolver = (
            self._type_registry.build_type_resolver(user_id)
            if self._type_registry
            else None
        )
        graph_service = create_graph_service(
            sketch_id=str(sketch_id),
            enable_batching=False,
            type_resolver=resolver,
        )
        graph_data = graph_service.get_sketch_graph()

        if format == "json":
            return {
                "sketch": {
                    "id": str(sketch.id),
                    "title": sketch.title,
                    "description": sketch.description,
                },
                "nodes": [node.model_dump(mode="json") for node in graph_data.nodes],
                "edges": [edge.model_dump(mode="json") for edge in graph_data.edges],
            }
        else:
            raise ValidationError(f"Unsupported format: {format}")


def create_sketch_service(db: Session) -> SketchService:
    from ..repositories import CustomTypeRepository
    from .type_registry_service import TypeRegistryService

    return SketchService(
        db=db,
        sketch_repo=SketchRepository(db),
        investigation_repo=InvestigationRepository(db),
        type_registry_service=TypeRegistryService(
            db=db, custom_type_repo=CustomTypeRepository(db)
        ),
    )
