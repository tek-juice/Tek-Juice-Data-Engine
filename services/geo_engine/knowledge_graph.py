"""
DATA ENGINE — GEO Knowledge Graph Builder
Phase 4: Constructs entity relationship graphs from extracted entities.
Knowledge graphs improve AI model comprehension and citation accuracy
by providing explicit entity relationships in structured form.
"""

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class KGNode:
    """A node in the knowledge graph representing an entity."""
    id: str
    label: str
    entity_type: str
    properties: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None


@dataclass
class KGEdge:
    """A directed relationship between two knowledge graph nodes."""
    source_id: str
    target_id: str
    relationship: str
    weight: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeGraph:
    """A knowledge graph with nodes, edges, and serialisation helpers."""
    nodes: list[KGNode] = field(default_factory=list)
    edges: list[KGEdge] = field(default_factory=list)

    def add_node(self, node: KGNode) -> None:
        if not any(n.id == node.id for n in self.nodes):
            self.nodes.append(node)

    def add_edge(self, edge: KGEdge) -> None:
        self.edges.append(edge)

    def get_neighbours(self, node_id: str) -> list[KGNode]:
        """Return all nodes directly connected to the given node."""
        connected_ids = {
            e.target_id for e in self.edges if e.source_id == node_id
        } | {
            e.source_id for e in self.edges if e.target_id == node_id
        }
        return [n for n in self.nodes if n.id in connected_ids]

    def to_dict(self) -> dict:
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "nodes": [
                {"id": n.id, "label": n.label, "type": n.entity_type, **n.properties}
                for n in self.nodes
            ],
            "edges": [
                {
                    "source": e.source_id,
                    "target": e.target_id,
                    "relationship": e.relationship,
                    "weight": e.weight,
                }
                for e in self.edges
            ],
        }

    def to_jsonld(self) -> dict:
        """Export as JSON-LD knowledge graph."""
        graph = []
        for node in self.nodes:
            graph.append({
                "@id":          f"urn:data-engine:entity:{node.id}",
                "@type":        f"schema:{node.entity_type}",
                "schema:name":  node.label,
                **{f"schema:{k}": v for k, v in node.properties.items()},
            })
        for edge in self.edges:
            graph.append({
                "@type":                "schema:Action",
                "schema:agent":         {"@id": f"urn:data-engine:entity:{edge.source_id}"},
                "schema:object":        {"@id": f"urn:data-engine:entity:{edge.target_id}"},
                "schema:actionStatus":  edge.relationship,
                "schema:weight":        edge.weight,
            })
        return {
            "@context": "https://schema.org",
            "@graph": graph,
        }


class KnowledgeGraphBuilder:
    """
    Builds knowledge graphs from entity lists and relationship inference.
    Relationships are inferred from co-occurrence proximity in text.
    """

    CO_OCCURRENCE_WINDOW = 200  # characters

    def build(
        self,
        content: str,
        entities: list[dict],
        document_id: str = "",
    ) -> KnowledgeGraph:
        """
        Build a knowledge graph from extracted entities.

        Args:
            content:     Original document text.
            entities:    Extracted entity dicts from EntityMapper.
            document_id: Source document identifier.

        Returns:
            KnowledgeGraph with nodes and inferred edges.
        """
        kg = KnowledgeGraph()

        for entity in entities:
            node_id = self._entity_id(entity["text"])
            kg.add_node(KGNode(
                id=node_id,
                label=entity["text"],
                entity_type=entity.get("entity_type", "Concept"),
                properties={
                    "confidence": entity.get("confidence", 0.5),
                    "document_id": document_id,
                    "wikidata_id": entity.get("wikidata_id"),
                },
            ))

        # Infer co-occurrence relationships
        edges = self._infer_co_occurrence_edges(content, entities)
        for edge in edges:
            kg.add_edge(edge)

        logger.debug(
            "knowledge_graph_built",
            document_id=document_id,
            nodes=len(kg.nodes),
            edges=len(kg.edges),
        )
        return kg

    def _infer_co_occurrence_edges(
        self, content: str, entities: list[dict]
    ) -> list[KGEdge]:
        """
        Create edges between entities that appear within
        CO_OCCURRENCE_WINDOW characters of each other.
        """
        edges: list[KGEdge] = []
        for i, ent_a in enumerate(entities):
            for ent_b in entities[i + 1:]:
                start_a = ent_a.get("char_start", 0)
                start_b = ent_b.get("char_start", 0)
                if abs(start_a - start_b) <= self.CO_OCCURRENCE_WINDOW:
                    rel = self._infer_relationship(
                        ent_a.get("entity_type", "Concept"),
                        ent_b.get("entity_type", "Concept"),
                    )
                    edges.append(KGEdge(
                        source_id=self._entity_id(ent_a["text"]),
                        target_id=self._entity_id(ent_b["text"]),
                        relationship=rel,
                        weight=round(
                            1.0 - abs(start_a - start_b) / self.CO_OCCURRENCE_WINDOW, 3
                        ),
                    ))
        return edges

    @staticmethod
    def _infer_relationship(type_a: str, type_b: str) -> str:
        """Map entity type pairs to relationship labels."""
        rel_map = {
            ("Person",       "Organisation"): "memberOf",
            ("Organisation", "Place"):        "locatedIn",
            ("Product",      "Organisation"): "createdBy",
            ("Person",       "Product"):      "created",
            ("Technology",   "Organisation"): "developedBy",
        }
        return rel_map.get((type_a, type_b), rel_map.get((type_b, type_a), "relatedTo"))

    @staticmethod
    def _entity_id(text: str) -> str:
        import re
        return re.sub(r'[^a-z0-9_]', '_', text.lower().strip())
