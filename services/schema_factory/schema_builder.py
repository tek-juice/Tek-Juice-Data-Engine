"""
DATA ENGINE — Schema Builder
The central orchestrator for GEO (Generative Engine Optimisation) schema generation.

Addresses Challenge 1 — Zero-Click & Entity-Matching GEO Bottleneck:
  - Builds heavy Schema.org JSON-LD with sameAs authority links
  - Enforces the First Sentence Rule (machine-scannable upfront answer)
  - Generates FAQPage, Organization, Product, Article with entity trust signals
  - Produces citation-ready structured data for AI Overviews, Perplexity, ChatGPT

Key GEO principles enforced:
  1. First Sentence Rule: content leads with a direct, factual answer
  2. Entity Authority: sameAs links to Wikidata, Wikipedia, official profiles
  3. Schema Richness: nested entities, relationships, and semantic context
  4. LLM Citation Signals: structured lists, tables, statistics in content
"""

from __future__ import annotations

import re
import json
from datetime import datetime, UTC
from dataclasses import dataclass, field
from typing import Any

import structlog

from configs.constants import SchemaType, SCHEMA_ORG_CONTEXT, EntityType
from configs.settings import get_settings
from services.schema_factory.jsonld import JSONLDGenerator
from services.schema_factory.metadata import MetadataGenerator
from services.schema_factory.ontology import OntologyBuilder

logger = structlog.get_logger(__name__)
settings = get_settings()


# ─────────────────────────────────────────────
# Authority sameAs profile templates
# LLMs use these to verify entity identity and trust
# ─────────────────────────────────────────────
AUTHORITY_BASE_URLS: dict[str, list[str]] = {
    "organisation": [
        "https://www.wikidata.org/wiki/",
        "https://en.wikipedia.org/wiki/",
        "https://www.linkedin.com/company/",
        "https://www.crunchbase.com/organization/",
    ],
    "person": [
        "https://www.wikidata.org/wiki/",
        "https://en.wikipedia.org/wiki/",
        "https://www.linkedin.com/in/",
        "https://orcid.org/",
    ],
    "product": [
        "https://www.g2.com/products/",
        "https://www.capterra.com/",
    ],
    "place": [
        "https://www.wikidata.org/wiki/",
        "https://en.wikipedia.org/wiki/",
    ],
}


@dataclass
class GEOEntity:
    """A named entity with authority sameAs links for GEO trust signals."""
    name: str
    entity_type: str                        # Person, Organisation, Product, Place
    wikidata_id: str = ""                   # e.g. Q12345
    wikipedia_slug: str = ""                # e.g. OpenAI
    linkedin_slug: str = ""                 # e.g. openai
    custom_same_as: list[str] = field(default_factory=list)
    description: str = ""
    founding_date: str = ""
    url: str = ""

    def build_same_as(self) -> list[str]:
        """Build sameAs URL list from all configured authority profiles."""
        urls: list[str] = []
        etype = self.entity_type.lower()

        if self.wikidata_id:
            urls.append(f"https://www.wikidata.org/wiki/{self.wikidata_id}")
        if self.wikipedia_slug:
            urls.append(f"https://en.wikipedia.org/wiki/{self.wikipedia_slug}")
        if self.linkedin_slug and etype == "organisation":
            urls.append(f"https://www.linkedin.com/company/{self.linkedin_slug}")
        if self.linkedin_slug and etype == "person":
            urls.append(f"https://www.linkedin.com/in/{self.linkedin_slug}")

        urls.extend(self.custom_same_as)
        return list(dict.fromkeys(urls))  # deduplicate preserving order


@dataclass
class SchemaBundle:
    """All schema outputs for a document in one structured package."""
    document_id: str
    tenant_id: str
    schema_type: str
    jsonld: dict[str, Any]
    metadata_html: str
    open_graph: dict[str, str]
    twitter_card: dict[str, str]
    ontology: dict[str, Any]
    first_sentence: str
    geo_entities: list[dict[str, Any]]
    same_as_urls: list[str]
    citation_score: float               # 0-1 score of LLM citation readiness
    script_tag: str                     # ready-to-embed HTML <script> tag


class SchemaBuilder:
    """
    Central GEO schema builder. Orchestrates JSON-LD generation,
    sameAs authority linking, First Sentence Rule enforcement,
    and citation readiness scoring.

    Usage:
        builder = SchemaBuilder()
        bundle = await builder.build(
            document_id="uuid",
            tenant_id="uuid",
            content="Full document text...",
            schema_type="Article",
            entities=[GEOEntity(name="OpenAI", entity_type="Organisation", ...)],
            metadata={"title": "...", "url": "..."},
        )
    """

    def __init__(self) -> None:
        self._jsonld    = JSONLDGenerator()
        self._metadata  = MetadataGenerator()
        self._ontology  = OntologyBuilder()

    async def build(
        self,
        document_id: str,
        tenant_id: str,
        content: str,
        schema_type: str = SchemaType.ARTICLE.value,
        entities: list[GEOEntity] | None = None,
        metadata: dict[str, Any] | None = None,
        missing_topics: list[str] | None = None,
    ) -> SchemaBundle:
        """
        Build a complete GEO-optimised schema bundle.

        Args:
            document_id:    UUID of the source document.
            tenant_id:      UUID of the tenant.
            content:        Full document text.
            schema_type:    Schema.org type to generate.
            entities:       Named entities with authority profiles.
            metadata:       Override fields (title, url, description, etc.).
            missing_topics: Topics from gap analysis to address in schema.

        Returns:
            SchemaBundle with all formats and GEO signals.
        """
        entities    = entities or []
        metadata    = metadata or {}
        missing_topics = missing_topics or []

        # 1. Enforce First Sentence Rule
        first_sentence = self._enforce_first_sentence_rule(content, metadata)

        # 2. Build enriched metadata with First Sentence as description
        enriched_meta = {
            **metadata,
            "description": first_sentence,
            "keywords": missing_topics[:10],
        }

        # 3. Build JSON-LD schema for the requested type
        jsonld = self._build_jsonld(
            schema_type=schema_type,
            content=content,
            entities=entities,
            meta=enriched_meta,
        )

        # 4. Collect all sameAs URLs from entities
        all_same_as: list[str] = []
        for entity in entities:
            all_same_as.extend(entity.build_same_as())
        all_same_as = list(dict.fromkeys(all_same_as))

        # 5. Add sameAs to Organisation/Article schemas
        if entities and schema_type in (
            SchemaType.ARTICLE.value,
            SchemaType.WEB_PAGE.value,
        ):
            jsonld["about"] = self._build_about_entities(entities)
        elif schema_type == SchemaType.ORGANISATION.value and entities:
            jsonld["sameAs"] = all_same_as

        # 6. Generate metadata bundle
        meta_bundle = self._metadata.generate(
            content=content,
            metadata=enriched_meta,
        )

        # 7. Build ontology from entities + missing topics
        all_topics = [e.name for e in entities] + missing_topics
        ontology = self._ontology.build_from_topics(all_topics)

        # 8. Score citation readiness
        citation_score = self._score_citation_readiness(
            content=content,
            jsonld=jsonld,
            entities=entities,
            first_sentence=first_sentence,
        )

        # 9. Persist to database
        bundle = SchemaBundle(
            document_id=document_id,
            tenant_id=tenant_id,
            schema_type=schema_type,
            jsonld=jsonld,
            metadata_html=self._metadata.to_html_string(meta_bundle),
            open_graph=meta_bundle.open_graph,
            twitter_card=meta_bundle.twitter_card,
            ontology=ontology.to_dict(),
            first_sentence=first_sentence,
            geo_entities=[self._entity_to_dict(e) for e in entities],
            same_as_urls=all_same_as,
            citation_score=citation_score,
            script_tag=self._jsonld.to_script_tag(jsonld),
        )

        await self._persist(bundle)

        logger.info(
            "schema_bundle_built",
            document_id=document_id,
            schema_type=schema_type,
            entities=len(entities),
            same_as_count=len(all_same_as),
            citation_score=round(citation_score, 2),
        )
        return bundle

    # ─────────────────────────────────────────────
    # First Sentence Rule
    # ─────────────────────────────────────────────

    def _enforce_first_sentence_rule(
        self, content: str, meta: dict
    ) -> str:
        """
        Extract or generate a direct, machine-scannable first sentence.

        GEO principle: LLMs strongly prefer content that answers the
        implicit question in the first sentence without requiring parsing.
        The sentence should contain: WHO/WHAT + IS/DOES + specific fact/stat.

        Examples of compliant first sentences:
          ✓ "OpenAI is an AI research company that developed GPT-4, used by 100M+ users."
          ✓ "Vector databases store high-dimensional embeddings for sub-millisecond similarity search."
          ✗ "In this article, we will explore..." (non-compliant — no direct answer)
          ✗ "There are many aspects to consider..." (non-compliant — vague)
        """
        # Use explicit description if provided and compliant
        if meta.get("description"):
            desc = meta["description"].strip()
            if self._is_first_sentence_compliant(desc):
                return desc[:300]

        # Extract from content
        sentences = re.split(r'(?<=[.!?])\s+', content.strip())
        for sentence in sentences[:5]:
            sentence = sentence.strip()
            if len(sentence) < 20:
                continue
            if self._is_first_sentence_compliant(sentence):
                return sentence[:300]

        # Fallback: use first clean sentence regardless
        if sentences:
            return sentences[0].strip()[:300]
        return content[:300].strip()

    @staticmethod
    def _is_first_sentence_compliant(sentence: str) -> bool:
        """
        Check if a sentence follows the First Sentence Rule.
        Must be direct, specific, and start with a noun/entity.
        """
        non_compliant_starters = (
            "in this", "this article", "we will", "today we",
            "there are", "it is important", "many people",
            "the purpose of", "let's explore", "welcome to",
        )
        lower = sentence.lower().strip()
        if any(lower.startswith(s) for s in non_compliant_starters):
            return False
        # Must be at least 30 chars and contain a verb
        if len(sentence) < 30:
            return False
        return True

    # ─────────────────────────────────────────────
    # JSON-LD builders per schema type
    # ─────────────────────────────────────────────

    def _build_jsonld(
        self,
        schema_type: str,
        content: str,
        entities: list[GEOEntity],
        meta: dict,
    ) -> dict[str, Any]:
        """Dispatch to the correct JSON-LD builder with entity enrichment."""
        builders = {
            SchemaType.ARTICLE.value:              self._build_article,
            SchemaType.FAQ_PAGE.value:             self._build_faq_page,
            SchemaType.HOW_TO.value:               self._build_how_to,
            SchemaType.PRODUCT.value:              self._build_product,
            SchemaType.ORGANISATION.value:         self._build_organisation,
            SchemaType.WEB_PAGE.value:             self._build_web_page,
            SchemaType.DATASET.value:              self._build_dataset,
            SchemaType.SOFTWARE_APPLICATION.value: self._build_software_app,
        }
        builder = builders.get(schema_type, self._build_article)
        return builder(content=content, entities=entities, meta=meta)

    def _build_article(
        self, content: str, entities: list[GEOEntity], meta: dict
    ) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "@context":      SCHEMA_ORG_CONTEXT,
            "@type":         "Article",
            "headline":      meta.get("title", self._first_line(content)),
            "description":   meta.get("description", content[:200]),
            "articleBody":   content[:5000],
            "datePublished": meta.get("date_published", datetime.now(UTC).strftime("%Y-%m-%d")),
            "dateModified":  datetime.now(UTC).strftime("%Y-%m-%d"),
            "inLanguage":    meta.get("language", "en"),
            "keywords":      meta.get("keywords", []),
            "author": {
                "@type": "Organisation",
                "name":  meta.get("author", settings.app_name),
                "url":   meta.get("author_url", ""),
            },
            "publisher": {
                "@type": "Organisation",
                "name":  meta.get("publisher", settings.app_name),
                "url":   meta.get("publisher_url", ""),
                "logo":  {
                    "@type": "ImageObject",
                    "url":   meta.get("logo_url", ""),
                },
            },
        }
        if meta.get("url"):
            schema["url"] = meta["url"]
        if meta.get("image"):
            schema["image"] = {"@type": "ImageObject", "url": meta["image"]}
        if meta.get("word_count"):
            schema["wordCount"] = meta["word_count"]
        return schema

    def _build_faq_page(
        self, content: str, entities: list[GEOEntity], meta: dict
    ) -> dict[str, Any]:
        """
        FAQPage is the highest-value schema for GEO citation.
        AI engines heavily cite structured Q&A content.
        Each answer must follow the First Sentence Rule.
        """
        faq_items = meta.get("faq_items") or self._extract_faqs(content)

        # Enforce First Sentence Rule on each answer
        validated_faqs = []
        for item in faq_items[:15]:
            answer = item.get("answer", "")
            if not self._is_first_sentence_compliant(answer):
                # Prepend a direct summary sentence
                answer = f"{item.get('question', '').rstrip('?')} — {answer}"
            validated_faqs.append({
                "@type": "Question",
                "name":  item.get("question", ""),
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text":  answer[:500],
                },
            })

        return {
            "@context":   SCHEMA_ORG_CONTEXT,
            "@type":      "FAQPage",
            "name":       meta.get("title", self._first_line(content)),
            "description": meta.get("description", content[:200]),
            "dateModified": datetime.now(UTC).strftime("%Y-%m-%d"),
            "mainEntity": validated_faqs,
        }

    def _build_how_to(
        self, content: str, entities: list[GEOEntity], meta: dict
    ) -> dict[str, Any]:
        steps = meta.get("steps") or self._extract_steps(content)
        return {
            "@context":    SCHEMA_ORG_CONTEXT,
            "@type":       "HowTo",
            "name":        meta.get("title", self._first_line(content)),
            "description": meta.get("description", content[:200]),
            "dateModified": datetime.now(UTC).strftime("%Y-%m-%d"),
            "totalTime":   meta.get("total_time", "PT10M"),
            "step": [
                {
                    "@type":       "HowToStep",
                    "position":    i + 1,
                    "name":        step[:100],
                    "text":        step,
                    "url":         f"{meta.get('url', '')}#step-{i + 1}",
                }
                for i, step in enumerate(steps[:10])
            ],
        }

    def _build_product(
        self, content: str, entities: list[GEOEntity], meta: dict
    ) -> dict[str, Any]:
        product_entities = [e for e in entities if e.entity_type.lower() == "product"]
        same_as = []
        for e in product_entities:
            same_as.extend(e.build_same_as())

        schema: dict[str, Any] = {
            "@context":    SCHEMA_ORG_CONTEXT,
            "@type":       "Product",
            "name":        meta.get("name", self._first_line(content)),
            "description": meta.get("description", content[:300]),
            "dateModified": datetime.now(UTC).strftime("%Y-%m-%d"),
            "brand": {
                "@type": "Brand",
                "name":  meta.get("brand", ""),
            },
        }
        if same_as:
            schema["sameAs"] = same_as
        if meta.get("url"):
            schema["url"] = meta["url"]
        if meta.get("image"):
            schema["image"] = meta["image"]
        if meta.get("price"):
            schema["offers"] = {
                "@type":         "Offer",
                "price":         meta["price"],
                "priceCurrency": meta.get("currency", "USD"),
                "availability":  "https://schema.org/InStock",
            }
        return schema

    def _build_organisation(
        self, content: str, entities: list[GEOEntity], meta: dict
    ) -> dict[str, Any]:
        """
        Organisation schema with maximum sameAs authority links.
        sameAs is the #1 signal for LLM entity verification.
        """
        org_entities = [
            e for e in entities
            if e.entity_type.lower() in ("organisation", "organization")
        ]
        all_same_as: list[str] = list(meta.get("same_as", []))
        for e in org_entities:
            all_same_as.extend(e.build_same_as())
        all_same_as = list(dict.fromkeys(all_same_as))

        schema: dict[str, Any] = {
            "@context":    SCHEMA_ORG_CONTEXT,
            "@type":       "Organisation",
            "name":        meta.get("name", self._first_line(content)),
            "description": meta.get("description", content[:300]),
            "url":         meta.get("url", ""),
            "sameAs":      all_same_as,
            "dateModified": datetime.now(UTC).strftime("%Y-%m-%d"),
        }
        if meta.get("logo"):
            schema["logo"] = {"@type": "ImageObject", "url": meta["logo"]}
        if meta.get("founding_date"):
            schema["foundingDate"] = meta["founding_date"]
        if meta.get("email"):
            schema["email"] = meta["email"]
        if meta.get("telephone"):
            schema["telephone"] = meta["telephone"]
        if meta.get("address"):
            schema["address"] = {
                "@type":           "PostalAddress",
                "streetAddress":   meta["address"].get("street", ""),
                "addressLocality": meta["address"].get("city", ""),
                "addressRegion":   meta["address"].get("region", ""),
                "postalCode":      meta["address"].get("postal_code", ""),
                "addressCountry":  meta["address"].get("country", ""),
            }
        if meta.get("social_profiles"):
            schema["sameAs"] = list(dict.fromkeys(
                all_same_as + meta["social_profiles"]
            ))
        return schema

    def _build_web_page(
        self, content: str, entities: list[GEOEntity], meta: dict
    ) -> dict[str, Any]:
        return {
            "@context":    SCHEMA_ORG_CONTEXT,
            "@type":       "WebPage",
            "name":        meta.get("title", self._first_line(content)),
            "description": meta.get("description", content[:200]),
            "url":         meta.get("url", ""),
            "inLanguage":  meta.get("language", "en"),
            "dateModified": datetime.now(UTC).strftime("%Y-%m-%d"),
            "about":       self._build_about_entities(entities) if entities else [],
        }

    def _build_dataset(
        self, content: str, entities: list[GEOEntity], meta: dict
    ) -> dict[str, Any]:
        return {
            "@context":    SCHEMA_ORG_CONTEXT,
            "@type":       "Dataset",
            "name":        meta.get("name", self._first_line(content)),
            "description": meta.get("description", content[:300]),
            "dateModified": datetime.now(UTC).strftime("%Y-%m-%d"),
            "license":     meta.get("license", "https://creativecommons.org/licenses/by/4.0/"),
            "creator": {
                "@type": "Organisation",
                "name":  meta.get("creator", settings.app_name),
            },
            "keywords": meta.get("keywords", []),
        }

    def _build_software_app(
        self, content: str, entities: list[GEOEntity], meta: dict
    ) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "@context":            SCHEMA_ORG_CONTEXT,
            "@type":               "SoftwareApplication",
            "name":                meta.get("name", self._first_line(content)),
            "description":         meta.get("description", content[:300]),
            "applicationCategory": meta.get("category", "BusinessApplication"),
            "operatingSystem":     meta.get("os", "All"),
            "dateModified":        datetime.now(UTC).strftime("%Y-%m-%d"),
        }
        if meta.get("url"):
            schema["url"] = meta["url"]
        if meta.get("price"):
            schema["offers"] = {
                "@type":         "Offer",
                "price":         meta["price"],
                "priceCurrency": meta.get("currency", "USD"),
            }
        return schema

    # ─────────────────────────────────────────────
    # Entity helpers
    # ─────────────────────────────────────────────

    @staticmethod
    def _build_about_entities(entities: list[GEOEntity]) -> list[dict]:
        """Build Schema.org 'about' entity array with sameAs links."""
        result = []
        for entity in entities:
            same_as = entity.build_same_as()
            node: dict[str, Any] = {
                "@type": entity.entity_type,
                "name":  entity.name,
            }
            if same_as:
                node["sameAs"] = same_as
            if entity.description:
                node["description"] = entity.description
            if entity.url:
                node["url"] = entity.url
            result.append(node)
        return result

    @staticmethod
    def _entity_to_dict(entity: GEOEntity) -> dict:
        return {
            "name":        entity.name,
            "entity_type": entity.entity_type,
            "wikidata_id": entity.wikidata_id,
            "same_as":     entity.build_same_as(),
            "description": entity.description,
            "url":         entity.url,
        }

    # ─────────────────────────────────────────────
    # Citation readiness scoring
    # ─────────────────────────────────────────────

    def _score_citation_readiness(
        self,
        content: str,
        jsonld: dict,
        entities: list[GEOEntity],
        first_sentence: str,
    ) -> float:
        """
        Score content for LLM citation readiness (0.0 – 1.0).
        Higher score = more likely to be cited by AI engines.

        Scoring criteria:
          - First sentence compliance (+0.25)
          - Has sameAs links (+0.20)
          - Has structured lists/tables (+0.15)
          - Has statistics/numbers (+0.15)
          - Schema type is FAQ or HowTo (+0.15)
          - Has author/publisher info (+0.10)
        """
        score = 0.0

        # First sentence compliance
        if self._is_first_sentence_compliant(first_sentence):
            score += 0.25

        # sameAs links present
        has_same_as = (
            jsonld.get("sameAs") or
            any(e.build_same_as() for e in entities) or
            any(
                "sameAs" in str(v)
                for v in jsonld.get("about", [])
            )
        )
        if has_same_as:
            score += 0.20

        # Structured content signals (lists, numbers)
        has_lists = bool(re.search(r'(?:^|\n)\s*[-•*]\s+\w', content, re.MULTILINE))
        has_numbers = bool(re.search(r'\b\d+(?:\.\d+)?(?:%|million|billion|thousand|K|M|B)\b', content, re.IGNORECASE))
        if has_lists:
            score += 0.10
        if has_numbers:
            score += 0.05

        # Schema type bonus
        high_value_types = (SchemaType.FAQ_PAGE.value, SchemaType.HOW_TO.value)
        if jsonld.get("@type") in high_value_types:
            score += 0.15

        # Has mainEntity (FAQPage) — highest citation signal
        if jsonld.get("mainEntity"):
            score += 0.10

        # Author/publisher present
        if jsonld.get("author") or jsonld.get("publisher"):
            score += 0.10

        # Has keywords
        if jsonld.get("keywords"):
            score += 0.05

        return round(min(1.0, score), 3)

    # ─────────────────────────────────────────────
    # Content extraction helpers
    # ─────────────────────────────────────────────

    @staticmethod
    def _first_line(content: str) -> str:
        return content.strip().split("\n")[0][:100].strip()

    @staticmethod
    def _extract_faqs(content: str) -> list[dict]:
        """Extract Q&A pairs from content using regex heuristics."""
        questions = re.findall(
            r'(?:^|\n)((?:What|How|Why|When|Where|Who|Can|Is|Are|Does|Should|Will)[^?\n]+\?)',
            content,
            re.MULTILINE,
        )
        faqs = []
        for q in questions[:10]:
            q = q.strip()
            # Try to find the answer — text after the question
            idx = content.find(q)
            answer = ""
            if idx >= 0:
                after = content[idx + len(q):].strip()
                sentences = re.split(r'(?<=[.!?])\s+', after)
                answer = " ".join(sentences[:3])[:400]
            faqs.append({"question": q, "answer": answer or "See full content for details."})
        return faqs

    @staticmethod
    def _extract_steps(content: str) -> list[str]:
        """Extract numbered steps from content."""
        steps = re.findall(r'(?:^|\n)\s*\d+[.)]\s*(.+)', content)
        return [s.strip() for s in steps[:10]] or [content[:200]]

    # ─────────────────────────────────────────────
    # Persistence
    # ─────────────────────────────────────────────

    async def _persist(self, bundle: SchemaBundle) -> None:
        """Save schema bundle to generated_schemas table."""
        try:
            from configs.database import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as session:
                await session.execute(
                    text("""
                        INSERT INTO generated_schemas
                            (document_id, tenant_id, schema_type, format,
                             content, metadata, is_active)
                        VALUES
                            (:document_id, :tenant_id, :schema_type, 'jsonld',
                             :content::jsonb, :metadata::jsonb, TRUE)
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "document_id": bundle.document_id,
                        "tenant_id":   bundle.tenant_id,
                        "schema_type": bundle.schema_type,
                        "content":     json.dumps(bundle.jsonld),
                        "metadata": json.dumps({
                            "first_sentence":  bundle.first_sentence,
                            "same_as_urls":    bundle.same_as_urls,
                            "citation_score":  bundle.citation_score,
                            "geo_entities":    bundle.geo_entities,
                            "open_graph":      bundle.open_graph,
                            "twitter_card":    bundle.twitter_card,
                        }),
                    },
                )
                await session.commit()
                logger.debug("schema_bundle_persisted", document_id=bundle.document_id)
        except Exception as exc:
            logger.error("schema_bundle_persist_failed", error=str(exc))
