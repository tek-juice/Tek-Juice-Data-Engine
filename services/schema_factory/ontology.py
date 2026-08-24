"""
DATA ENGINE — Ontology Builder
Constructs lightweight ontologies and concept hierarchies
from document entities and relationships.
Phase 2: Schema expansion and structured relationship mapping.
"""

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class OntologyNode:
    """A concept node in the ontology graph."""
    id: str
    label: str
    type: str                          # e.g. 'Concept', 'Technology', 'Process'
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    broader: list[str] = field(default_factory=list)   # Parent concept IDs
    narrower: list[str] = field(default_factory=list)  # Child concept IDs
    related: list[str] = field(default_factory=list)   # Related concept IDs
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class Ontology:
    """A collection of nodes and relationships forming a concept ontology."""
    name: str
    nodes: list[OntologyNode] = field(default_factory=list)
    root_ids: list[str] = field(default_factory=list)

    def add_node(self, node: OntologyNode) -> None:
        self.nodes.append(node)

    def get_node(self, node_id: str) -> OntologyNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "node_count": len(self.nodes),
            "nodes": [
                {
                    "id":          n.id,
                    "label":       n.label,
                    "type":        n.type,
                    "description": n.description,
                    "broader":     n.broader,
                    "narrower":    n.narrower,
                    "related":     n.related,
                }
                for n in self.nodes
            ],
        }

    def to_jsonld(self) -> dict:
        """Export ontology as JSON-LD using SKOS vocabulary."""
        graph = []
        for node in self.nodes:
            entry: dict = {
                "@id":           f"urn:data-engine:concept:{node.id}",
                "@type":         "skos:Concept",
                "skos:prefLabel": {"@value": node.label, "@language": "en"},
            }
            if node.description:
                entry["skos:definition"] = {"@value": node.description, "@language": "en"}
            if node.aliases:
                entry["skos:altLabel"] = [{"@value": a, "@language": "en"} for a in node.aliases]
            if node.broader:
                entry["skos:broader"] = [{"@id": f"urn:data-engine:concept:{b}"} for b in node.broader]
            if node.narrower:
                entry["skos:narrower"] = [{"@id": f"urn:data-engine:concept:{n}"} for n in node.narrower]
            if node.related:
                entry["skos:related"] = [{"@id": f"urn:data-engine:concept:{r}"} for r in node.related]
            graph.append(entry)

        return {
            "@context": {
                "skos": "http://www.w3.org/2004/02/skos/core#",
                "xsd":  "http://www.w3.org/2001/XMLSchema#",
            },
            "@graph": graph,
        }


class OntologyBuilder:
    """
    Constructs an ontology from a list of entity/concept strings.
    Uses simple heuristics for hierarchy inference — for production,
    replace with an LLM-driven hierarchy builder (see llm_factory.py).
    """

    def build_from_topics(self, topics: list[str], domain: str = "general") -> Ontology:
        """
        Build an ontology from a flat list of topic strings.

        Args:
            topics: List of concept/topic strings to organise.
            domain: Domain name for the ontology root.

        Returns:
            Ontology with nodes and inferred relationships.
        """
        ontology = Ontology(name=f"{domain}_ontology")
        seen_ids: set[str] = set()

        for i, topic in enumerate(topics):
            node_id = self._to_id(topic)
            if node_id in seen_ids:
                continue
            seen_ids.add(node_id)

            node = OntologyNode(
                id=node_id,
                label=topic,
                type=self._classify_type(topic),
            )
            # Simple heuristic: multi-word topics are narrower than single-word ones
            words = topic.split()
            if len(words) > 2:
                # Look for a broader concept
                shorter = " ".join(words[:2])
                broader_id = self._to_id(shorter)
                if broader_id in seen_ids:
                    node.broader.append(broader_id)
                    broader_node = ontology.get_node(broader_id)
                    if broader_node:
                        broader_node.narrower.append(node_id)

            ontology.add_node(node)

        logger.debug("ontology_built", domain=domain, nodes=len(ontology.nodes))
        return ontology

    @staticmethod
    def _to_id(label: str) -> str:
        import re
        return re.sub(r"[^a-z0-9_]", "_", label.lower().strip())

    @staticmethod
    def _classify_type(topic: str) -> str:
        tech_keywords = {"api", "model", "vector", "embedding", "neural", "llm", "ai", "ml"}
        process_keywords = {"analysis", "processing", "detection", "generation", "optimization"}
        words = set(topic.lower().split())
        if words & tech_keywords:
            return "Technology"
        if words & process_keywords:
            return "Process"
        return "Concept"
