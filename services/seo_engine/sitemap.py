"""
DATA ENGINE — Sitemap Generator
Phase 4: Generates XML sitemaps compliant with the Sitemap Protocol 0.9.
Used to improve search engine indexing and crawl prioritisation.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Literal

import structlog

logger = structlog.get_logger(__name__)

ChangeFreq = Literal["always", "hourly", "daily", "weekly", "monthly", "yearly", "never"]


@dataclass
class SitemapURL:
    """A single URL entry in the sitemap."""
    loc: str                          # Canonical URL
    lastmod: str | None = None        # YYYY-MM-DD
    changefreq: ChangeFreq = "weekly"
    priority: float = 0.5             # 0.0–1.0


@dataclass
class SitemapResult:
    """Generated sitemap with XML content and statistics."""
    url_count: int
    xml_content: str
    generated_at: str


class SitemapGenerator:
    """
    Generates XML sitemaps from a list of URL entries.
    Supports both standard sitemaps and sitemap index files
    for large sites (> 50,000 URLs).
    """

    MAX_URLS_PER_SITEMAP = 50_000

    def generate(self, urls: list[SitemapURL]) -> SitemapResult:
        """
        Generate a standard XML sitemap.

        Args:
            urls: List of SitemapURL entries to include.

        Returns:
            SitemapResult with XML content and statistics.
        """
        if len(urls) > self.MAX_URLS_PER_SITEMAP:
            logger.warning(
                "sitemap_url_limit_exceeded",
                count=len(urls),
                limit=self.MAX_URLS_PER_SITEMAP,
            )
            urls = urls[:self.MAX_URLS_PER_SITEMAP]

        # Build XML
        ET.register_namespace("", "http://www.sitemaps.org/schemas/sitemap/0.9")
        urlset = ET.Element(
            "urlset",
            xmlns="http://www.sitemaps.org/schemas/sitemap/0.9",
        )

        for entry in urls:
            url_elem = ET.SubElement(urlset, "url")
            ET.SubElement(url_elem, "loc").text = entry.loc
            if entry.lastmod:
                ET.SubElement(url_elem, "lastmod").text = entry.lastmod
            ET.SubElement(url_elem, "changefreq").text = entry.changefreq
            ET.SubElement(url_elem, "priority").text = str(entry.priority)

        xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
            urlset, encoding="unicode", xml_declaration=False
        )

        logger.debug("sitemap_generated", url_count=len(urls))
        return SitemapResult(
            url_count=len(urls),
            xml_content=xml_str,
            generated_at=datetime.now(UTC).isoformat(),
        )

    def generate_index(self, sitemap_urls: list[str]) -> str:
        """
        Generate a sitemap index XML file that references multiple sitemaps.

        Args:
            sitemap_urls: List of sitemap file URLs.

        Returns:
            XML string of the sitemap index.
        """
        sitemapindex = ET.Element(
            "sitemapindex",
            xmlns="http://www.sitemaps.org/schemas/sitemap/0.9",
        )

        for url in sitemap_urls:
            sitemap_elem = ET.SubElement(sitemapindex, "sitemap")
            ET.SubElement(sitemap_elem, "loc").text = url
            ET.SubElement(sitemap_elem, "lastmod").text = datetime.now(UTC).strftime("%Y-%m-%d")

        return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
            sitemapindex, encoding="unicode"
        )

    def from_document_list(
        self,
        base_url: str,
        documents: list[dict],
        default_priority: float = 0.6,
    ) -> SitemapResult:
        """
        Build a sitemap from a list of document metadata dicts.

        Args:
            base_url:         Base domain URL (e.g. https://example.com).
            documents:        List of dicts with: slug, updated_at, priority (optional).
            default_priority: Default priority if not specified per document.

        Returns:
            SitemapResult with generated XML.
        """
        urls = []
        for doc in documents:
            slug = doc.get("slug", doc.get("id", ""))
            lastmod = None
            updated_at = doc.get("updated_at")
            if updated_at:
                if hasattr(updated_at, "strftime"):
                    lastmod = updated_at.strftime("%Y-%m-%d")
                else:
                    lastmod = str(updated_at)[:10]

            urls.append(SitemapURL(
                loc=f"{base_url.rstrip('/')}/{slug}",
                lastmod=lastmod,
                changefreq="weekly",
                priority=float(doc.get("priority", default_priority)),
            ))

        return self.generate(urls)
