"""
DATA ENGINE — Metadata Generator
Generates Open Graph, Twitter Card, and basic HTML meta tags
from document content and extracted entities.
Phase 2: Metadata creation for SEO and GEO optimisation.
"""

from dataclasses import dataclass
from typing import Any

import structlog

from configs.constants import MetadataFormat

logger = structlog.get_logger(__name__)


@dataclass
class MetadataBundle:
    """All metadata formats for a document in one place."""
    title: str
    description: str
    keywords: list[str]
    open_graph: dict[str, str]
    twitter_card: dict[str, str]
    html_meta: list[dict[str, str]]
    canonical_url: str | None = None


class MetadataGenerator:
    """
    Generates metadata bundles from document content.
    Supports Open Graph, Twitter Card, and standard HTML meta tags.
    """

    MAX_DESCRIPTION_LENGTH = 160
    MAX_TITLE_LENGTH = 70

    def generate(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
        target_format: str = MetadataFormat.OPEN_GRAPH.value,
    ) -> MetadataBundle:
        """
        Generate a metadata bundle from content.

        Args:
            content:        Source document text.
            metadata:       Optional override fields.
            target_format:  Primary format to optimise for.

        Returns:
            MetadataBundle with all formats populated.
        """
        meta = metadata or {}

        title = self._build_title(content, meta)
        description = self._build_description(content, meta)
        keywords = self._extract_keywords(content, meta)

        og = self._open_graph(title, description, meta)
        tc = self._twitter_card(title, description, meta)
        html = self._html_meta(title, description, keywords, meta)

        bundle = MetadataBundle(
            title=title,
            description=description,
            keywords=keywords,
            open_graph=og,
            twitter_card=tc,
            html_meta=html,
            canonical_url=meta.get("url"),
        )

        logger.debug("metadata_generated", title=title[:50], format=target_format)
        return bundle

    def _build_title(self, content: str, meta: dict) -> str:
        if meta.get("title"):
            return meta["title"][:self.MAX_TITLE_LENGTH]
        first_line = content.strip().split("\n")[0]
        return first_line[:self.MAX_TITLE_LENGTH].strip()

    def _build_description(self, content: str, meta: dict) -> str:
        if meta.get("description"):
            return meta["description"][:self.MAX_DESCRIPTION_LENGTH]
        # Use first meaningful paragraph
        paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 50]
        if paragraphs:
            return paragraphs[0][:self.MAX_DESCRIPTION_LENGTH]
        return content[:self.MAX_DESCRIPTION_LENGTH].strip()

    @staticmethod
    def _extract_keywords(content: str, meta: dict) -> list[str]:
        if meta.get("keywords"):
            return meta["keywords"][:15]
        # Simple frequency-based keyword extraction
        import re
        words = re.findall(r'\b[a-zA-Z]{4,}\b', content.lower())
        stopwords = {"that", "this", "with", "from", "they", "have", "been",
                     "will", "more", "also", "than", "then", "when", "what"}
        freq: dict[str, int] = {}
        for word in words:
            if word not in stopwords:
                freq[word] = freq.get(word, 0) + 1
        return [w for w, _ in sorted(freq.items(), key=lambda x: x[1], reverse=True)[:10]]

    @staticmethod
    def _open_graph(title: str, description: str, meta: dict) -> dict[str, str]:
        og = {
            "og:type":        meta.get("og_type", "article"),
            "og:title":       title,
            "og:description": description,
            "og:site_name":   meta.get("site_name", "DATA ENGINE"),
        }
        if meta.get("url"):
            og["og:url"] = meta["url"]
        if meta.get("image"):
            og["og:image"] = meta["image"]
        return og

    @staticmethod
    def _twitter_card(title: str, description: str, meta: dict) -> dict[str, str]:
        tc = {
            "twitter:card":        meta.get("twitter_card_type", "summary_large_image"),
            "twitter:title":       title,
            "twitter:description": description,
        }
        if meta.get("twitter_site"):
            tc["twitter:site"] = meta["twitter_site"]
        if meta.get("image"):
            tc["twitter:image"] = meta["image"]
        return tc

    @staticmethod
    def _html_meta(title: str, description: str, keywords: list[str], meta: dict) -> list[dict[str, str]]:
        tags = [
            {"name": "description", "content": description},
            {"name": "keywords",    "content": ", ".join(keywords)},
            {"name": "robots",      "content": meta.get("robots", "index, follow")},
        ]
        if meta.get("author"):
            tags.append({"name": "author", "content": meta["author"]})
        return tags

    def to_html_string(self, bundle: MetadataBundle) -> str:
        """Render all meta tags as HTML string."""
        lines = [f'<title>{bundle.title}</title>']

        for tag in bundle.html_meta:
            lines.append(f'<meta name="{tag["name"]}" content="{tag["content"]}">')

        for prop, content in bundle.open_graph.items():
            lines.append(f'<meta property="{prop}" content="{content}">')

        for name, content in bundle.twitter_card.items():
            lines.append(f'<meta name="{name}" content="{content}">')

        if bundle.canonical_url:
            lines.append(f'<link rel="canonical" href="{bundle.canonical_url}">')

        return "\n".join(lines)
