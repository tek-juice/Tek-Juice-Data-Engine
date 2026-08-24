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
  5. Zero-Click Design: brand name + UVP woven into the first sentence so the
     impression is captured even if the user never clicks through to the page.
     Pattern enforced: "{Brand} is/provides {specific value} — {proof point}."
"""

from __future__ import annotations

import re
import json
from datetime import datetime, UTC
from dataclasses import dataclass, field
from typing import Any

import structlog

from configs.constants import SchemaType, SCHEMA_ORG_CONTEXT
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
    zero_click_score: float = 0.0       # 0-1 score of zero-click impression quality
    zero_click_sentence: str = ""       # optimised zero-click first sentence
    eeeat_score: float = 0.0            # E-E-A-T composite score (0-1)
    speakable_script_tag: str = ""      # SpeakableSpecification JSON-LD for voice
    breadcrumb_script_tag: str = ""     # BreadcrumbList JSON-LD


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

        # 2. Zero-Click Design: build brand+UVP sentence for AI chat interfaces
        zero_click_sentence = self._build_zero_click_sentence(
            content=content,
            first_sentence=first_sentence,
            metadata=metadata,
            entities=entities,
        )
        zero_click_score = self._score_zero_click(zero_click_sentence, metadata)

        # 3. Use zero-click sentence as the canonical description
        enriched_meta = {
            **metadata,
            "description": zero_click_sentence or first_sentence,
            "keywords": missing_topics[:10],
        }

        # 4. Build JSON-LD schema for the requested type
        jsonld = self._build_jsonld(
            schema_type=schema_type,
            content=content,
            entities=entities,
            meta=enriched_meta,
        )

        # 5. Collect all sameAs URLs from entities
        all_same_as: list[str] = []
        for entity in entities:
            all_same_as.extend(entity.build_same_as())
        all_same_as = list(dict.fromkeys(all_same_as))

        # 6. Add sameAs to Organisation/Article schemas
        if entities and schema_type in (
            SchemaType.ARTICLE.value,
            SchemaType.WEB_PAGE.value,
        ):
            jsonld["about"] = self._build_about_entities(entities)
        elif schema_type == SchemaType.ORGANISATION.value and entities:
            jsonld["sameAs"] = all_same_as

        # 7. Generate metadata bundle
        meta_bundle = self._metadata.generate(
            content=content,
            metadata=enriched_meta,
        )

        # 8. Build ontology from entities + missing topics
        all_topics = [e.name for e in entities] + missing_topics
        ontology = self._ontology.build_from_topics(all_topics)

        # 9. Score citation readiness
        citation_score = self._score_citation_readiness(
            content=content,
            jsonld=jsonld,
            entities=entities,
            first_sentence=first_sentence,
        )

        # 10. E-E-A-T signals (Experience, Expertise, Authoritativeness, Trustworthiness)
        eeeat_score = self._score_eeeat(content=content, jsonld=jsonld, metadata=metadata)
        # Inject E-E-A-T fields into JSON-LD
        self._inject_eeeat(jsonld, metadata, eeeat_score)

        # 11. SpeakableSpecification for Google voice search + Google Discover
        speakable_jsonld = self._build_speakable(
            url=metadata.get("url", ""),
            first_sentence=first_sentence,
            zero_click_sentence=zero_click_sentence,
        )

        # 12. BreadcrumbList for navigation signals
        breadcrumb_jsonld = self._build_breadcrumb(metadata)

        # 13. Persist to database
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
            zero_click_score=zero_click_score,
            zero_click_sentence=zero_click_sentence,
            eeeat_score=eeeat_score,
            speakable_script_tag=self._jsonld.to_script_tag(speakable_jsonld) if speakable_jsonld else "",
            breadcrumb_script_tag=self._jsonld.to_script_tag(breadcrumb_jsonld) if breadcrumb_jsonld else "",
        )

        await self._persist(bundle)

        logger.info(
            "schema_bundle_built",
            document_id=document_id,
            schema_type=schema_type,
            entities=len(entities),
            same_as_count=len(all_same_as),
            citation_score=round(citation_score, 2),
            zero_click_score=round(zero_click_score, 2),
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
    # Zero-Click Content Design
    # ─────────────────────────────────────────────

    def _build_zero_click_sentence(
        self,
        content: str,
        first_sentence: str,
        metadata: dict,
        entities: list[GEOEntity],
    ) -> str:
        """
        Build a zero-click optimised sentence for AI chat interfaces.

        Zero-Click Design principle:
          Even if the user never clicks through to the page, the AI response
          surfaces the brand name and unique value proposition directly in the
          chat window. This requires the very first sentence surfaced in any
          AI citation to follow the pattern:

            "{Brand} {is/provides/offers} {specific value} — {proof point}."

          Examples:
            ✓ "Tek Juice is a multi-channel inventory platform that syncs stock
               across Shopify, Amazon, and TikTok Shop in real time."
            ✓ "DataEngine provides AI-ready structured data pipelines that
               reduce content indexing latency by 60%."
            ✗ "Learn how our platform can help your business grow." (no brand,
               no specifics)

        The method attempts four sources in priority order:
          1. Explicit brand + description in metadata
          2. First compliant sentence already containing the brand
          3. Constructed sentence from entity + existing first_sentence
          4. Original first_sentence unchanged
        """
        brand = (
            metadata.get("brand_name")
            or metadata.get("publisher")
            or metadata.get("author")
            or settings.app_name
        )
        uvp = metadata.get("uvp") or metadata.get("value_proposition") or ""

        # Priority 1: explicit brand + UVP in metadata
        if brand and uvp:
            candidate = f"{brand} {uvp}"
            if self._is_zero_click_compliant(candidate, brand):
                return candidate[:300]

        # Priority 2: existing first_sentence already mentions brand
        if brand and brand.lower() in first_sentence.lower():
            if self._is_zero_click_compliant(first_sentence, brand):
                return first_sentence[:300]

        # Priority 3: construct from organisation entity + first_sentence
        org_entities = [
            e for e in entities
            if e.entity_type.lower() in ("organisation", "organization")
        ]
        if org_entities:
            org = org_entities[0]
            constructed = f"{org.name} — {first_sentence}"
            if len(constructed.split()) <= 50:
                return constructed[:300]
            # Trim to 40 words
            words = constructed.split()[:40]
            return " ".join(words) + "."

        # Priority 4: prepend brand to first_sentence if not present
        if brand and brand.lower() not in first_sentence.lower():
            candidate = f"{brand}: {first_sentence}"
            if len(candidate) <= 300:
                return candidate

        return first_sentence[:300]

    @staticmethod
    def _is_zero_click_compliant(sentence: str, brand: str) -> bool:
        """
        Check if a sentence satisfies zero-click design requirements:
          - Contains the brand name
          - Is direct and specific (not vague)
          - Is at least 20 characters
        """
        if not sentence or len(sentence) < 20:
            return False
        if brand and brand.lower() not in sentence.lower():
            return False
        non_compliant_starters = (
            "learn how", "discover", "find out", "click here",
            "we offer", "welcome", "our services", "in this",
        )
        lower = sentence.lower().strip()
        if any(lower.startswith(s) for s in non_compliant_starters):
            return False
        return True

    @staticmethod
    def _score_zero_click(sentence: str, metadata: dict) -> float:
        """
        Score a zero-click sentence on a 0.0–1.0 scale.

        Criteria:
          - Brand name present      (+0.30)
          - UVP/value present       (+0.25) — verb + specific noun
          - Proof point present     (+0.20) — number, stat, or named feature
          - Length optimal 15–50w   (+0.15)
          - Not a filler opener     (+0.10)
        """
        if not sentence:
            return 0.0

        score = 0.0
        brand = (
            metadata.get("brand_name")
            or metadata.get("publisher")
            or metadata.get("author")
            or ""
        )
        words = sentence.split()

        # Brand present
        if brand and brand.lower() in sentence.lower():
            score += 0.30

        # Contains a value verb (is, provides, offers, enables, delivers, etc.)
        value_verbs = (
            " is ", " are ", " provides ", " offers ", " enables ",
            " delivers ", " powers ", " helps ", " gives ", " builds ",
        )
        if any(v in f" {sentence.lower()} " for v in value_verbs):
            score += 0.25

        # Proof point (number, %, named feature)
        if re.search(r'\b\d+(?:\.\d+)?(?:%|x|\s?times|k|m|b)?\b', sentence, re.IGNORECASE):
            score += 0.20
        elif re.search(r'\b(?:real.?time|instant|automated|ai-powered|enterprise)\b', sentence, re.IGNORECASE):
            score += 0.10

        # Optimal length
        if 15 <= len(words) <= 50:
            score += 0.15

        # Not a filler opener
        filler = ("learn how", "discover", "click here", "in this", "welcome")
        if not any(sentence.lower().startswith(f) for f in filler):
            score += 0.10

        return round(min(1.0, score), 3)

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
            # VSEO multi-modal types
            SchemaType.IMAGE_OBJECT.value:         self._build_image_object,
            SchemaType.VIDEO_OBJECT.value:         self._build_video_object,
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
                "@type": "Organization",
                "name":  meta.get("author", settings.app_name),
                "url":   meta.get("author_url", ""),
            },
            "publisher": {
                "@type": "Organization",
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
            "@type":       "Organization",
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
                "@type": "Organization",
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

    def _build_image_object(
        self, content: str, entities: list[GEOEntity], meta: dict
    ) -> dict[str, Any]:
        """
        Build a Schema.org ImageObject for VSEO multi-modal indexing.
        meta keys: url, content_url, caption, width, height, author, license_url
        """
        schema: dict[str, Any] = {
            "@context":   SCHEMA_ORG_CONTEXT,
            "@type":      "ImageObject",
            "url":        meta.get("url", ""),
            "description": meta.get("description", content[:200]),
            "dateModified": datetime.now(UTC).strftime("%Y-%m-%d"),
        }
        if meta.get("content_url"):
            schema["contentUrl"] = meta["content_url"]
        if meta.get("caption"):
            schema["caption"] = meta["caption"]
        if meta.get("width"):
            schema["width"] = meta["width"]
        if meta.get("height"):
            schema["height"] = meta["height"]
        if meta.get("author"):
            schema["author"] = {"@type": "Person", "name": meta["author"]}
        if meta.get("license_url"):
            schema["license"] = meta["license_url"]
        return schema

    def _build_video_object(
        self, content: str, entities: list[GEOEntity], meta: dict
    ) -> dict[str, Any]:
        """
        Build a Schema.org VideoObject with Clip entities for VSEO.
        meta keys: url, name, thumbnail_url, duration, upload_date,
                   embed_url, transcript, author
        """
        schema: dict[str, Any] = {
            "@context":   SCHEMA_ORG_CONTEXT,
            "@type":      "VideoObject",
            "name":       meta.get("name", self._first_line(content)),
            "description": meta.get("description", content[:300]),
            "uploadDate": meta.get("upload_date", datetime.now(UTC).strftime("%Y-%m-%d")),
            "dateModified": datetime.now(UTC).strftime("%Y-%m-%d"),
        }
        if meta.get("url"):
            schema["url"] = meta["url"]
        if meta.get("thumbnail_url"):
            schema["thumbnailUrl"] = meta["thumbnail_url"]
        if meta.get("duration"):
            schema["duration"] = meta["duration"]          # ISO 8601 e.g. PT2M30S
        if meta.get("embed_url"):
            schema["embedUrl"] = meta["embed_url"]
        if meta.get("author"):
            schema["author"] = {"@type": "Person", "name": meta["author"]}
        if meta.get("transcript"):
            schema["transcript"] = meta["transcript"][:5000]
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
    # E-E-A-T signals
    # ─────────────────────────────────────────────

    @staticmethod
    def _score_eeeat(content: str, jsonld: dict, metadata: dict) -> float:
        """
        Score E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness).
        Google's Quality Rater Guidelines weight these signals heavily for AI Overviews.

        Scoring (0.0–1.0):
          Experience:       author bio present, first-person signals       (+0.15)
          Expertise:        credentials, certifications, publication venue (+0.20)
          Authoritativeness: sameAs links, backlink signals, org schema    (+0.25)
          Trustworthiness:  date signals, source citations, HTTPS, review (+0.25)
          Completeness:     word count ≥ 600, statistics present           (+0.15)
        """
        score = 0.0
        lower = content.lower()

        # Experience — author bio / first-person signals
        has_author = bool(metadata.get("author") or jsonld.get("author"))
        has_byline = bool(re.search(r'\b(?:written by|author:|by [A-Z])\b', content, re.IGNORECASE))
        if has_author or has_byline:
            score += 0.15

        # Expertise — credentials or known publication venue
        expertise_signals = re.findall(
            r'\b(?:PhD|MD|MBA|professor|researcher|certified|expert|according to|published in|study|research|report)\b',
            content, re.IGNORECASE
        )
        score += min(0.20, len(expertise_signals) * 0.04)

        # Authoritativeness — sameAs present + schema type trust
        has_same_as = bool(jsonld.get("sameAs") or jsonld.get("about"))
        if has_same_as:
            score += 0.15
        if metadata.get("domain_authority") and float(metadata["domain_authority"]) > 30:
            score += 0.10

        # Trustworthiness — date, sources, citations
        has_date = bool(
            jsonld.get("datePublished") or jsonld.get("dateModified") or
            re.search(r'\b(20\d{2})\b', content)
        )
        if has_date:
            score += 0.10
        source_signals = re.findall(
            r'\b(?:source:|via|according to|cited from|references?:|study by|data from)\b',
            content, re.IGNORECASE
        )
        score += min(0.15, len(source_signals) * 0.05)

        # Completeness
        word_count = len(content.split())
        if word_count >= 600:
            score += 0.08
        has_stats = bool(re.search(r'\b\d+(?:\.\d+)?(?:%|x|times|k|m|b|million|billion)\b', content, re.IGNORECASE))
        if has_stats:
            score += 0.07

        return round(min(1.0, score), 3)

    @staticmethod
    def _inject_eeeat(jsonld: dict, metadata: dict, eeeat_score: float) -> None:
        """
        Inject E-E-A-T signals directly into the JSON-LD object in-place.
        Adds author credentials, publication/update dates, and review signals.
        """
        now_date = datetime.now(UTC).strftime("%Y-%m-%d")

        # Ensure datePublished and dateModified are always present
        if not jsonld.get("datePublished"):
            jsonld["datePublished"] = metadata.get("date_published", now_date)
        if not jsonld.get("dateModified"):
            jsonld["dateModified"] = now_date

        # Author with credentials
        if not jsonld.get("author") and metadata.get("author"):
            jsonld["author"] = {
                "@type": "Person",
                "name":  metadata["author"],
                "url":   metadata.get("author_url", ""),
            }
        if metadata.get("author_credentials"):
            if isinstance(jsonld.get("author"), dict):
                jsonld["author"]["description"] = metadata["author_credentials"]

        # Publisher with logo
        if not jsonld.get("publisher"):
            jsonld["publisher"] = {
                "@type": "Organization",
                "name":  metadata.get("publisher", settings.app_name),
                "url":   metadata.get("publisher_url", ""),
                "logo":  {"@type": "ImageObject", "url": metadata.get("logo_url", "")},
            }

        # Review/rating signals (social proof for Trustworthiness)
        if metadata.get("review_count") and metadata.get("review_rating"):
            jsonld["aggregateRating"] = {
                "@type":       "AggregateRating",
                "ratingValue": str(metadata["review_rating"]),
                "reviewCount": str(metadata["review_count"]),
                "bestRating":  "5",
            }

        # Cite as / isPartOf for authority signals
        if metadata.get("url"):
            jsonld["mainEntityOfPage"] = {
                "@type": "WebPage",
                "@id":   metadata["url"],
            }

    # ─────────────────────────────────────────────
    # Voice search: SpeakableSpecification
    # ─────────────────────────────────────────────

    @staticmethod
    def _build_speakable(
        url: str,
        first_sentence: str,
        zero_click_sentence: str,
    ) -> dict | None:
        """
        Build a SpeakableSpecification JSON-LD block.
        Google uses this to select content for voice search and Google Discover.
        The speakable section must contain the most important sentence(s) that
        directly answer a likely voice query about the content.
        """
        if not url and not first_sentence:
            return None

        speakable_text = zero_click_sentence or first_sentence
        if len(speakable_text) < 20:
            return None

        return {
            "@context": "https://schema.org",
            "@type":    "WebPage",
            "url":      url,
            "speakable": {
                "@type":     "SpeakableSpecification",
                "cssSelector": ["h1", ".speakable", "article > p:first-of-type"],
                "xpath": [
                    "/html/head/title",
                    "/html/head/meta[@name='description']/@content",
                ],
            },
            "name":        speakable_text[:100],
            "description": speakable_text[:300],
        }

    # ─────────────────────────────────────────────
    # Navigation: BreadcrumbList
    # ─────────────────────────────────────────────

    @staticmethod
    def _build_breadcrumb(metadata: dict) -> dict | None:
        """
        Build a BreadcrumbList JSON-LD block from metadata breadcrumbs.
        Breadcrumbs are a strong signal for Google to understand page hierarchy
        and are displayed in SERP snippets — direct click-through rate booster.

        metadata keys:
          breadcrumbs: list of {"name": "...", "url": "..."} dicts, ordered root→leaf
          url: page URL used as final crumb if breadcrumbs not provided
        """
        breadcrumbs = metadata.get("breadcrumbs", [])

        # Auto-generate from URL if not provided
        if not breadcrumbs and metadata.get("url"):
            url = metadata["url"]
            parts = url.rstrip("/").split("/")
            breadcrumbs = []
            for i, part in enumerate(parts):
                if part.startswith("http"):
                    breadcrumbs.append({"name": "Home", "url": "/".join(parts[:3]) + "/"})
                elif part:
                    crumb_url = "/".join(parts[:i + 1]) + "/"
                    name = part.replace("-", " ").replace("_", " ").title()
                    breadcrumbs.append({"name": name, "url": crumb_url})

        if not breadcrumbs:
            return None

        return {
            "@context": "https://schema.org",
            "@type":    "BreadcrumbList",
            "itemListElement": [
                {
                    "@type":    "ListItem",
                    "position": i + 1,
                    "name":     crumb.get("name", ""),
                    "item":     crumb.get("url", ""),
                }
                for i, crumb in enumerate(breadcrumbs[:8])
            ],
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
                                            "first_sentence":       bundle.first_sentence,
                                            "zero_click_sentence":  bundle.zero_click_sentence,
                                            "zero_click_score":     bundle.zero_click_score,
                                            "same_as_urls":         bundle.same_as_urls,
                                            "citation_score":       bundle.citation_score,
                                            "eeeat_score":          bundle.eeeat_score,
                                            "geo_entities":         bundle.geo_entities,
                                            "open_graph":           bundle.open_graph,
                                            "twitter_card":         bundle.twitter_card,
                                            "speakable":            bool(bundle.speakable_script_tag),
                                            "breadcrumb":           bool(bundle.breadcrumb_script_tag),
                                        }),
                    },
                )
                await session.commit()
                logger.debug("schema_bundle_persisted", document_id=bundle.document_id)
        except Exception as exc:
            logger.error("schema_bundle_persist_failed", error=str(exc))
