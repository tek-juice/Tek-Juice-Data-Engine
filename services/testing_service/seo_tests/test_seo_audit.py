"""
DATA ENGINE — SEO Audit Tests
Phase 4: Automated SEO validation tests run before production release.
Tests keyword coverage, metadata compliance, structured data validity,
and sitemap integrity.
"""

import pytest
from services.seo_engine.keyword_analysis import KeywordAnalyser
from services.seo_engine.metadata import SEOMetadataOptimiser
from services.seo_engine.structured_data import StructuredDataValidator
from services.seo_engine.sitemap import SitemapGenerator, SitemapURL
from services.seo_engine.validators import SEOValidator


class TestKeywordAnalysis:

    def setup_method(self):
        self.analyser = KeywordAnalyser()

    def test_keyword_present(self):
        content = "Vector databases enable fast semantic search at scale."
        result = self.analyser.analyse(content, ["vector databases", "semantic search"])
        assert "vector databases" in result.matched_keywords
        assert "semantic search" in result.matched_keywords

    def test_keyword_missing(self):
        content = "This document discusses machine learning pipelines."
        result = self.analyser.analyse(content, ["vector database", "embedding model"])
        assert len(result.missing_keywords) == 2
        assert result.coverage_score == 0.0

    def test_coverage_score_range(self):
        content = "AI and machine learning drive modern data processing pipelines."
        result = self.analyser.analyse(content, ["AI", "machine learning", "quantum computing"])
        assert 0.0 <= result.coverage_score <= 1.0

    def test_keyword_density_calculation(self):
        content = "vector " * 10 + "other words " * 90
        result = self.analyser.analyse(content, ["vector"])
        assert "vector" in result.keyword_density
        assert result.keyword_density["vector"] > 0

    def test_natural_keyword_extraction(self):
        content = "semantic search uses vector embeddings to find similar documents efficiently"
        keywords = self.analyser.extract_natural_keywords(content, top_n=5)
        assert len(keywords) > 0
        assert all(isinstance(kw, tuple) and len(kw) == 2 for kw in keywords)

    def test_density_issue_flagged_for_overuse(self):
        content = "vector " * 50 + "text " * 2
        result = self.analyser.analyse(content, ["vector"])
        assert len(result.density_issues) > 0


class TestSEOMetadata:

    def setup_method(self):
        self.optimiser = SEOMetadataOptimiser()

    def test_title_length_ok(self):
        result = self.optimiser.audit(
            title="Semantic Search with Vector Embeddings",
            meta_description="Learn how vector embeddings enable fast semantic search at scale in production systems.",
        )
        assert result.score > 50

    def test_title_too_short(self):
        result = self.optimiser.audit(title="AI", meta_description="")
        assert any("too short" in issue for issue in result.title_issues)

    def test_title_too_long(self):
        long_title = "A" * 80
        result = self.optimiser.audit(title=long_title, meta_description="A valid meta description that is long enough for SEO purposes.")
        assert any("too long" in issue for issue in result.title_issues)

    def test_missing_meta_description(self):
        result = self.optimiser.audit(title="Valid Title for Testing", meta_description="")
        assert any("missing" in issue for issue in result.meta_desc_issues)

    def test_keyword_in_title(self):
        result = self.optimiser.audit(
            title="Vector Database Guide",
            meta_description="Complete guide to vector databases for AI applications.",
            primary_keyword="vector database",
        )
        assert result.score >= 60

    def test_missing_keyword_in_title_flagged(self):
        result = self.optimiser.audit(
            title="A Generic Title Without the Keyword",
            meta_description="Some description.",
            primary_keyword="vector embeddings",
        )
        assert any("vector embeddings" in issue for issue in result.title_issues)

    def test_title_suggestion_generation(self):
        suggestions = self.optimiser.generate_title_suggestions(
            keywords=["vector database", "semantic search"],
            content_excerpt="Store and retrieve high-dimensional embeddings",
        )
        assert len(suggestions) >= 2
        assert all(isinstance(s, str) for s in suggestions)


class TestStructuredData:

    def setup_method(self):
        self.validator = StructuredDataValidator()

    def test_valid_article_schema(self):
        schema = {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": "How Vector Databases Work",
            "author": {"@type": "Person", "name": "Jane Doe"},
            "datePublished": "2025-01-01",
            "description": "An in-depth look at vector database technology.",
        }
        result = self.validator.validate(schema)
        assert result.is_valid
        assert result.score >= 70

    def test_missing_required_field(self):
        schema = {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": "Title Only — No Author or Date",
        }
        result = self.validator.validate(schema)
        assert not result.is_valid
        assert any("author" in e.lower() for e in result.errors)

    def test_missing_context(self):
        schema = {"@type": "Article", "headline": "Test"}
        result = self.validator.validate(schema)
        assert not result.is_valid

    def test_valid_faq_schema(self):
        schema = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": "What is a vector database?",
                    "acceptedAnswer": {"@type": "Answer", "text": "A database for high-dimensional vectors."},
                }
            ],
        }
        result = self.validator.validate(schema)
        assert result.is_valid

    def test_invalid_faq_no_main_entity(self):
        schema = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": []}
        result = self.validator.validate(schema)
        assert not result.is_valid

    def test_invalid_json_string(self):
        result = self.validator.validate_json_string("{not valid json}")
        assert not result.is_valid
        assert "Invalid JSON" in result.errors[0]


class TestSitemap:

    def setup_method(self):
        self.generator = SitemapGenerator()

    def test_basic_sitemap_generation(self):
        urls = [
            SitemapURL(loc="https://example.com/page-1", priority=0.8),
            SitemapURL(loc="https://example.com/page-2", priority=0.5),
        ]
        result = self.generator.generate(urls)
        assert result.url_count == 2
        assert "<?xml" in result.xml_content
        assert "https://example.com/page-1" in result.xml_content

    def test_sitemap_from_documents(self):
        docs = [
            {"slug": "article-one", "updated_at": "2025-01-01"},
            {"slug": "article-two", "updated_at": "2025-06-15"},
        ]
        result = self.generator.from_document_list("https://example.com", docs)
        assert result.url_count == 2
        assert "article-one" in result.xml_content

    def test_sitemap_index_generation(self):
        index_xml = self.generator.generate_index([
            "https://example.com/sitemap-1.xml",
            "https://example.com/sitemap-2.xml",
        ])
        assert "sitemapindex" in index_xml
        assert "sitemap-1.xml" in index_xml


class TestSEOValidator:

    def setup_method(self):
        self.validator = SEOValidator()

    def test_valid_https_url(self):
        result = self.validator.audit_url("https://example.com/page")
        assert result.is_https
        assert result.is_valid_url

    def test_http_url_flagged(self):
        result = self.validator.audit_url("http://example.com/page")
        assert not result.is_https
        assert any("HTTPS" in issue for issue in result.issues)

    def test_invalid_url(self):
        result = self.validator.audit_url("not-a-url")
        assert not result.is_valid_url

    def test_robots_directive_valid(self):
        result = self.validator.validate_robots_directive("index, follow")
        assert result["is_valid"]

    def test_robots_directive_conflict(self):
        result = self.validator.validate_robots_directive("index, noindex")
        assert not result["is_valid"]
        assert len(result["conflicts"]) > 0
