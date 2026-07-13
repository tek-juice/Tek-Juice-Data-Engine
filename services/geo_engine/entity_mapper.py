"""
DATA ENGINE — GEO Entity Mapper
Phase 4: Extracts and classifies named entities from content for
Generative Engine Optimisation (GEO). Maps entities to Schema.org
types and Wikidata identifiers to improve AI model visibility.
"""

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ExtractedEntity:
    """A single named entity extracted from content."""
    text: str
    entity_type: str          # Person, Organisation, Place, Product, Technology, Concept
    confidence: float         # 0.0–1.0
    char_start: int
    char_end: int
    wikidata_id: str | None = None
    schema_org_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityMapResult:
    """Result of entity mapping on a document."""
    document_id: str
    entities: list[ExtractedEntity]
    entity_count: int
    type_distribution: dict[str, int]
    coverage_score: float     # fraction of content covered by entities


SCHEMA_TYPE_MAP = {
    "Person":       "https://schema.org/Person",
    "Organisation": "https://schema.org/Organization",
    "Place":        "https://schema.org/Place",
    "Product":      "https://schema.org/Product",
    "Event":        "https://schema.org/Event",
    "Technology":   "https://schema.org/SoftwareApplication",
    "Concept":      "https://schema.org/Thing",
}


class EntityMapper:
    """
    Extracts named entities and maps them to structured knowledge.

    Supports two modes:
    - spaCy NER (preferred, more accurate)
    - Rule-based fallback (no external dependency)
    """

    def __init__(self, use_spacy: bool = True) -> None:
        self._use_spacy = use_spacy
        self._nlp = None  # Lazy-loaded

    def extract(self, content: str, document_id: str = "") -> EntityMapResult:
        """
        Extract named entities from content.

        Args:
            content:     Plain text content.
            document_id: Optional document ID for tracking.

        Returns:
            EntityMapResult with all extracted entities.
        """
        if self._use_spacy:
            entities = self._extract_spacy(content)
        else:
            entities = self._extract_rule_based(content)

        type_distribution: dict[str, int] = {}
        for ent in entities:
            type_distribution[ent.entity_type] = type_distribution.get(ent.entity_type, 0) + 1

        covered_chars = sum(e.char_end - e.char_start for e in entities)
        coverage_score = min(1.0, covered_chars / len(content)) if content else 0.0

        logger.debug(
            "entity_mapping_complete",
            document_id=document_id,
            entity_count=len(entities),
            types=list(type_distribution.keys()),
        )
        return EntityMapResult(
            document_id=document_id,
            entities=entities,
            entity_count=len(entities),
            type_distribution=type_distribution,
            coverage_score=round(coverage_score, 4),
        )

    def _extract_spacy(self, content: str) -> list[ExtractedEntity]:
        """Use spaCy NER for entity extraction."""
        try:
            if self._nlp is None:
                import spacy
                self._nlp = spacy.load("en_core_web_sm")

            doc = self._nlp(content[:100_000])  # spaCy limit
            entities = []
            for ent in doc.ents:
                entity_type = self._map_spacy_label(ent.label_)
                entities.append(ExtractedEntity(
                    text=ent.text,
                    entity_type=entity_type,
                    confidence=0.85,
                    char_start=ent.start_char,
                    char_end=ent.end_char,
                    schema_org_type=SCHEMA_TYPE_MAP.get(entity_type),
                ))
            return entities
        except Exception as exc:
            logger.warning("spacy_extraction_failed_using_fallback", error=str(exc))
            return self._extract_rule_based(content)

    def _extract_rule_based(self, content: str) -> list[ExtractedEntity]:
        """
        Lightweight rule-based entity extraction.
        Detects capitalised phrases as potential named entities.
        """
        entities = []
        # Match sequences of capitalised words (2–4 words)
        pattern = r'\b([A-Z][a-z]+(?:\s[A-Z][a-z]+){0,3})\b'
        for match in re.finditer(pattern, content):
            text = match.group(0)
            if len(text) < 3 or text.lower() in {"the", "a", "an", "in", "on", "at"}:
                continue
            entity_type = self._guess_type(text)
            entities.append(ExtractedEntity(
                text=text,
                entity_type=entity_type,
                confidence=0.60,
                char_start=match.start(),
                char_end=match.end(),
                schema_org_type=SCHEMA_TYPE_MAP.get(entity_type),
            ))
        # Deduplicate by text
        seen: set[str] = set()
        unique = []
        for e in entities:
            if e.text not in seen:
                seen.add(e.text)
                unique.append(e)
        return unique

    @staticmethod
    def _map_spacy_label(label: str) -> str:
        """Map spaCy NER labels to DATA ENGINE entity types."""
        mapping = {
            "PERSON":  "Person",
            "ORG":     "Organisation",
            "GPE":     "Place",
            "LOC":     "Place",
            "PRODUCT": "Product",
            "EVENT":   "Event",
            "WORK_OF_ART": "Concept",
            "LAW":     "Concept",
            "LANGUAGE": "Concept",
            "NORP":    "Organisation",
            "FAC":     "Place",
        }
        return mapping.get(label, "Concept")

    @staticmethod
    def _guess_type(text: str) -> str:
        """Heuristic type classification for rule-based extraction."""
        tech_words = {"ai", "ml", "api", "sdk", "llm", "gpt", "bert", "python", "cloud"}
        place_suffixes = ("city", "town", "land", "shire", "berg", "burg", "ville")
        lower = text.lower()
        if any(t in lower for t in tech_words):
            return "Technology"
        if lower.endswith(place_suffixes):
            return "Place"
        return "Concept"
