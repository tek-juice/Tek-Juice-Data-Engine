"""
DATA ENGINE — GEO Entity Mapper
Phase 4: Extracts and classifies named entities from content for
Generative Engine Optimisation (GEO). Maps entities to Schema.org
types and resolves Wikidata sameAs identifiers to improve AI model
citation authority.

NER backend priority:
  1. spaCy en_core_web_trf (transformer — most accurate, GPU-optional)
  2. spaCy en_core_web_sm  (CPU pipeline — fast fallback)
  3. Rule-based heuristic  (zero-dependency last resort)

Wikidata linking runs async against the public Wikidata API and is
best-effort — entities without matches are still returned without a
wikidata_id.
"""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


# ── Schema.org type mapping ───────────────────────────────────────────────────

SCHEMA_TYPE_MAP: dict[str, str] = {
    "Person":       "https://schema.org/Person",
    "Organisation": "https://schema.org/Organization",
    "Place":        "https://schema.org/Place",
    "Product":      "https://schema.org/Product",
    "Event":        "https://schema.org/Event",
    "Technology":   "https://schema.org/SoftwareApplication",
    "Concept":      "https://schema.org/Thing",
}

# spaCy label → DATA ENGINE entity type
_SPACY_LABEL_MAP: dict[str, str] = {
    "PERSON":       "Person",
    "ORG":          "Organisation",
    "GPE":          "Place",
    "LOC":          "Place",
    "FAC":          "Place",
    "PRODUCT":      "Product",
    "EVENT":        "Event",
    "WORK_OF_ART":  "Concept",
    "LAW":          "Concept",
    "LANGUAGE":     "Concept",
    "NORP":         "Organisation",
    "MONEY":        "Concept",
    "PERCENT":      "Concept",
}

# Wikidata entity type → wbsearchentities type filter
_WIKIDATA_TYPE_FILTER: dict[str, str] = {
    "Person":       "item",
    "Organisation": "item",
    "Place":        "item",
    "Product":      "item",
    "Technology":   "item",
    "Event":        "item",
    "Concept":      "item",
}

WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
WIKIDATA_ENTITY_URL = "https://www.wikidata.org/wiki/{qid}"
WIKIDATA_SEARCH_LIMIT = 1           # first hit only — fastest lookup
WIKIDATA_REQUEST_TIMEOUT = 4.0      # seconds; keep tight so it never blocks pipeline
WIKIDATA_MAX_CONCURRENT = 5         # parallel lookups per batch


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ExtractedEntity:
    """A single named entity extracted from content."""
    text: str
    entity_type: str          # Person, Organisation, Place, Product, Technology, Concept
    confidence: float         # 0.0–1.0  (real spaCy score when available)
    char_start: int
    char_end: int
    wikidata_id: str | None = None          # e.g. "Q42"
    wikidata_url: str | None = None         # full URL for sameAs link
    schema_org_type: str | None = None      # full Schema.org URL
    same_as: list[str] = field(default_factory=list)  # all sameAs URIs
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text":            self.text,
            "entity_type":     self.entity_type,
            "confidence":      self.confidence,
            "char_start":      self.char_start,
            "char_end":        self.char_end,
            "wikidata_id":     self.wikidata_id,
            "wikidata_url":    self.wikidata_url,
            "schema_org_type": self.schema_org_type,
            "same_as":         self.same_as,
        }


@dataclass
class EntityMapResult:
    """Result of entity mapping on a document."""
    document_id: str
    entities: list[ExtractedEntity]
    entity_count: int
    type_distribution: dict[str, int]
    coverage_score: float       # fraction of content chars covered by entities
    wikidata_linked_count: int  # entities that resolved to a Wikidata QID


# ── Wikidata linker ───────────────────────────────────────────────────────────

class WikidataLinker:
    """
    Resolves entity text labels to Wikidata QIDs via the public
    wbsearchentities API.  Responses are cached in-process to avoid
    redundant network calls within a single request.
    """

    _cache: dict[str, str | None] = {}   # class-level: persists per worker process

    async def link(self, entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
        """
        Resolve Wikidata IDs for a list of entities (best-effort, in-place).

        Args:
            entities: Extracted entity list to enrich.

        Returns:
            The same list with wikidata_id / wikidata_url / same_as populated
            where a match was found.
        """
        # Only link high-value, concrete entity types
        linkable = [
            e for e in entities
            if e.entity_type in ("Person", "Organisation", "Place", "Product", "Technology")
            and e.confidence >= 0.70
        ]

        # Deduplicate text labels before hitting the API
        unique_texts = list({e.text for e in linkable})

        sem = asyncio.Semaphore(WIKIDATA_MAX_CONCURRENT)
        async with httpx.AsyncClient(timeout=WIKIDATA_REQUEST_TIMEOUT) as client:
            tasks = [self._lookup(client, sem, text) for text in unique_texts]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        resolved: dict[str, str] = {}
        for text, result in zip(unique_texts, results):
            if isinstance(result, str) and result:
                resolved[text] = result

        # Enrich entities in-place
        for entity in entities:
            qid = resolved.get(entity.text)
            if qid:
                entity.wikidata_id  = qid
                entity.wikidata_url = WIKIDATA_ENTITY_URL.format(qid=qid)
                entity.same_as = [
                    entity.wikidata_url,
                    f"https://en.wikipedia.org/wiki/{entity.text.replace(' ', '_')}",
                ]
                if entity.schema_org_type:
                    entity.same_as.append(entity.schema_org_type)

        return entities

    async def _lookup(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        text: str,
    ) -> str | None:
        """Single Wikidata search for one entity label. Returns QID or None."""
        if text in self._cache:
            return self._cache[text]

        try:
            async with sem:
                resp = await client.get(
                    WIKIDATA_API_URL,
                    params={
                        "action":   "wbsearchentities",
                        "search":   text,
                        "language": "en",
                        "limit":    WIKIDATA_SEARCH_LIMIT,
                        "format":   "json",
                    },
                    headers={"User-Agent": "DataEngine/1.0 (entity-linker)"},
                )
                resp.raise_for_status()
                data = resp.json()
                hits = data.get("search", [])
                qid = hits[0]["id"] if hits else None
                self._cache[text] = qid
                return qid
        except Exception as exc:
            logger.debug("wikidata_lookup_failed", entity=text, error=str(exc))
            self._cache[text] = None
            return None


# ── Main mapper ───────────────────────────────────────────────────────────────

class EntityMapper:
    """
    Extracts named entities and resolves them to structured knowledge.

    NER backend is selected at construction time with automatic fallback.
    Wikidata linking is performed async for all high-confidence entities.
    """

    def __init__(self, use_spacy: bool = True, wikidata_linking: bool = True) -> None:
        self._use_spacy        = use_spacy
        self._wikidata_linking = wikidata_linking
        self._nlp              = None   # lazy-loaded on first call
        self._linker           = WikidataLinker() if wikidata_linking else None

    # ── Public API ───────────────────────────────────────────────────────────

    async def extract_entities(
        self,
        content: str,
        document_id: str = "",
    ) -> list[ExtractedEntity]:
        """
        Extract named entities from content and resolve Wikidata IDs.

        Args:
            content:     Plain text content to analyse.
            document_id: Optional document ID for logging.

        Returns:
            List of ExtractedEntity objects, enriched with Wikidata links
            where available.
        """
        result = await self.extract(content, document_id)
        return result.entities

    async def extract(
        self,
        content: str,
        document_id: str = "",
    ) -> EntityMapResult:
        """
        Full extraction + Wikidata linking pipeline.

        Args:
            content:     Plain text content.
            document_id: Optional tracking ID.

        Returns:
            EntityMapResult with entities, distribution, and coverage.
        """
        if self._use_spacy:
            entities = self._extract_spacy(content)
        else:
            entities = self._extract_rule_based(content)

        # Wikidata linking (async, best-effort)
        if self._linker and entities:
            try:
                entities = await self._linker.link(entities)
            except Exception as exc:
                logger.warning("wikidata_linking_failed", error=str(exc))

        type_distribution: dict[str, int] = {}
        for ent in entities:
            type_distribution[ent.entity_type] = (
                type_distribution.get(ent.entity_type, 0) + 1
            )

        covered_chars = sum(e.char_end - e.char_start for e in entities)
        coverage_score = min(1.0, covered_chars / len(content)) if content else 0.0
        wikidata_linked = sum(1 for e in entities if e.wikidata_id)

        logger.debug(
            "entity_mapping_complete",
            document_id=document_id,
            entity_count=len(entities),
            wikidata_linked=wikidata_linked,
            types=list(type_distribution.keys()),
        )

        return EntityMapResult(
            document_id=document_id,
            entities=entities,
            entity_count=len(entities),
            type_distribution=type_distribution,
            coverage_score=round(coverage_score, 4),
            wikidata_linked_count=wikidata_linked,
        )

    # ── NER backends ─────────────────────────────────────────────────────────

    def _extract_spacy(self, content: str) -> list[ExtractedEntity]:
        """
        Run spaCy NER.  Attempts transformer model first, falls back to
        the small CPU pipeline, then to rule-based extraction.
        """
        try:
            if self._nlp is None:
                self._nlp = self._load_spacy_model()

            doc = self._nlp(content[:100_000])  # spaCy hard limit
            entities: list[ExtractedEntity] = []
            seen: set[str] = set()

            for ent in doc.ents:
                if ent.text in seen:
                    continue
                seen.add(ent.text)

                entity_type = _SPACY_LABEL_MAP.get(ent.label_, "Concept")

                # Real confidence: use entity KB score if present (transformer),
                # otherwise fall back to a label-based heuristic.
                confidence = self._ent_confidence(ent)

                entities.append(ExtractedEntity(
                    text=ent.text,
                    entity_type=entity_type,
                    confidence=confidence,
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
        Lightweight heuristic fallback: capitalised n-grams (1–4 words).
        No external dependency required.
        """
        entities: list[ExtractedEntity] = []
        pattern = r'\b([A-Z][a-z]+(?:\s[A-Z][a-z]+){0,3})\b'
        seen: set[str] = set()

        for match in re.finditer(pattern, content):
            text = match.group(0)
            if len(text) < 3 or text.lower() in {"the", "a", "an", "in", "on", "at"}:
                continue
            if text in seen:
                continue
            seen.add(text)

            entity_type = self._guess_type(text)
            entities.append(ExtractedEntity(
                text=text,
                entity_type=entity_type,
                confidence=0.55,    # low confidence — heuristic only
                char_start=match.start(),
                char_end=match.end(),
                schema_org_type=SCHEMA_TYPE_MAP.get(entity_type),
            ))

        return entities

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _load_spacy_model():
        """
        Load the best available spaCy model.
        Prefers the transformer pipeline (en_core_web_trf) for highest
        accuracy; falls back to en_core_web_sm for CPU-only environments.
        """
        import spacy

        for model_name in ("en_core_web_trf", "en_core_web_sm"):
            try:
                nlp = spacy.load(model_name)
                logger.info("spacy_model_loaded", model=model_name)
                return nlp
            except OSError:
                logger.debug("spacy_model_not_found", model=model_name)

        raise RuntimeError(
            "No spaCy model found. Run: "
            "python -m spacy download en_core_web_sm"
        )

    @staticmethod
    def _ent_confidence(ent) -> float:
        """
        Extract a real confidence value from a spaCy Span.
        Transformer pipelines expose ent._.trf_data; smaller models don't.
        Falls back to a fixed 0.85 for standard pipeline predictions.
        """
        try:
            # kb_score is set by the EntityLinker component (if present)
            if hasattr(ent._, "kb_qid") and ent._.kb_qid:
                return getattr(ent._, "score", 0.85)
        except Exception:
            pass
        return 0.85

    @staticmethod
    def _guess_type(text: str) -> str:
        """Heuristic type for rule-based extraction."""
        tech_words = {"ai", "ml", "api", "sdk", "llm", "gpt", "bert", "python", "cloud"}
        place_suffixes = ("city", "town", "land", "shire", "berg", "burg", "ville")
        lower = text.lower()
        if any(t in lower for t in tech_words):
            return "Technology"
        if any(lower.endswith(s) for s in place_suffixes):
            return "Place"
        return "Concept"
