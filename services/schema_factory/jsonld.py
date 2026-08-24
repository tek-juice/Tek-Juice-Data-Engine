"""
DATA ENGINE — JSON-LD Schema Generator
Phase 2: Creates structured Schema.org JSON-LD objects from document content.
Used by the LLM Schema Factory when gaps are detected.
"""

import json
from datetime import datetime, UTC
from typing import Any

import structlog

from configs.constants import SchemaType, SCHEMA_ORG_CONTEXT

logger = structlog.get_logger(__name__)


class JSONLDGenerator:
    """
    Generates valid Schema.org JSON-LD structured data.
    Supports Article, FAQPage, HowTo, Product, Organisation, WebPage, Dataset.
    """

    def generate(
        self,
        schema_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Generate a JSON-LD schema object.

        Args:
            schema_type: Schema.org type string (e.g. 'Article', 'FAQPage').
            content:     Source text to extract schema data from.
            metadata:    Optional override fields (name, description, url, etc.).

        Returns:
            Valid JSON-LD dict ready for embedding in <script type="application/ld+json">.
        """
        meta = metadata or {}
        generators = {
            SchemaType.ARTICLE.value:              self._article,
            SchemaType.FAQ_PAGE.value:             self._faq_page,
            SchemaType.HOW_TO.value:               self._how_to,
            SchemaType.PRODUCT.value:              self._product,
            SchemaType.ORGANISATION.value:         self._organisation,
            SchemaType.WEB_PAGE.value:             self._web_page,
            SchemaType.DATASET.value:              self._dataset,
            SchemaType.SOFTWARE_APPLICATION.value: self._software_application,
        }

        gen_fn = generators.get(schema_type, self._web_page)
        schema = gen_fn(content=content, meta=meta)
        logger.debug("jsonld_generated", schema_type=schema_type)
        return schema

    def _base(self, schema_type: str, meta: dict) -> dict:
        return {
            "@context": SCHEMA_ORG_CONTEXT,
            "@type": schema_type,
            "dateModified": datetime.now(UTC).strftime("%Y-%m-%d"),
        }

    def _article(self, content: str, meta: dict) -> dict:
        schema = self._base("Article", meta)
        schema.update({
            "headline":         meta.get("title", self._extract_title(content)),
            "description":      meta.get("description", content[:200].strip()),
            "articleBody":      content[:5000],
            "author":           {"@type": "Organization", "name": meta.get("author", "DATA ENGINE")},
            "publisher":        {"@type": "Organization", "name": meta.get("publisher", "DATA ENGINE")},
            "datePublished":    meta.get("date_published", datetime.now(UTC).strftime("%Y-%m-%d")),
            "inLanguage":       meta.get("language", "en"),
            "keywords":         meta.get("keywords", []),
        })
        if meta.get("url"):
            schema["url"] = meta["url"]
        return schema

    def _faq_page(self, content: str, meta: dict) -> dict:
        schema = self._base("FAQPage", meta)
        faq_items = meta.get("faq_items", [])
        if not faq_items:
            faq_items = self._extract_faqs_from_content(content)
        schema["mainEntity"] = [
            {
                "@type": "Question",
                "name": item.get("question", ""),
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": item.get("answer", ""),
                },
            }
            for item in faq_items[:10]
        ]
        return schema

    def _how_to(self, content: str, meta: dict) -> dict:
        schema = self._base("HowTo", meta)
        schema.update({
            "name":        meta.get("title", self._extract_title(content)),
            "description": meta.get("description", content[:200].strip()),
            "step": [
                {"@type": "HowToStep", "text": step}
                for step in meta.get("steps", self._extract_steps(content))[:10]
            ],
        })
        return schema

    def _product(self, content: str, meta: dict) -> dict:
        schema = self._base("Product", meta)
        schema.update({
            "name":        meta.get("name", self._extract_title(content)),
            "description": meta.get("description", content[:300].strip()),
            "brand":       {"@type": "Brand", "name": meta.get("brand", "")},
        })
        return schema

    def _organisation(self, content: str, meta: dict) -> dict:
        schema = self._base("Organisation", meta)
        schema.update({
            "name":        meta.get("name", ""),
            "description": meta.get("description", content[:200].strip()),
            "url":         meta.get("url", ""),
            "sameAs":      meta.get("same_as", []),
        })
        return schema

    def _web_page(self, content: str, meta: dict) -> dict:
        schema = self._base("WebPage", meta)
        schema.update({
            "name":            meta.get("title", self._extract_title(content)),
            "description":     meta.get("description", content[:200].strip()),
            "url":             meta.get("url", ""),
            "inLanguage":      meta.get("language", "en"),
        })
        return schema

    def _dataset(self, content: str, meta: dict) -> dict:
        schema = self._base("Dataset", meta)
        schema.update({
            "name":        meta.get("name", self._extract_title(content)),
            "description": meta.get("description", content[:300].strip()),
            "creator":     {"@type": "Organization", "name": meta.get("creator", "DATA ENGINE")},
            "license":     meta.get("license", "https://creativecommons.org/licenses/by/4.0/"),
        })
        return schema

    def _software_application(self, content: str, meta: dict) -> dict:
        schema = self._base("SoftwareApplication", meta)
        schema.update({
            "name":              meta.get("name", ""),
            "description":       meta.get("description", content[:300].strip()),
            "applicationCategory": meta.get("category", "BusinessApplication"),
            "operatingSystem":   meta.get("os", "All"),
        })
        return schema

    @staticmethod
    def _extract_title(content: str) -> str:
        """Extract the first sentence as a title fallback."""
        first_line = content.strip().split("\n")[0]
        return first_line[:100].strip()

    @staticmethod
    def _extract_faqs_from_content(content: str) -> list[dict]:
        """Heuristically extract Q&A pairs from content."""
        import re
        questions = re.findall(r"(?:^|\n)((?:What|How|Why|When|Where|Who|Can|Is|Are|Does)[^?\n]+\?)", content)
        faqs = []
        for q in questions[:5]:
            faqs.append({"question": q.strip(), "answer": "See full content for details."})
        return faqs

    @staticmethod
    def _extract_steps(content: str) -> list[str]:
        """Extract numbered steps from content."""
        import re
        steps = re.findall(r"(?:^|\n)\s*\d+[.)]\s*(.+)", content)
        return [s.strip() for s in steps[:10]] or [content[:200]]

    def to_script_tag(self, schema: dict) -> str:
        """Render schema as an HTML <script> tag."""
        return f'<script type="application/ld+json">\n{json.dumps(schema, indent=2)}\n</script>'
