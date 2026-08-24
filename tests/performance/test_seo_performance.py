"""
DATA ENGINE — Performance Tests: SEO & GEO Engines
Benchmarks for keyword analysis, metadata generation, structured data
validation, entity mapping, and citation readiness at scale.

Run with: pytest tests/performance/test_seo_performance.py -v -m performance
"""

import time
import pytest

pytestmark = pytest.mark.performance


class TestSEOPerformance:

    def test_structured_data_validation_batch_under_500ms(self):
        """Validate 100 JSON-LD schemas in under 500ms."""
        from services.seo_engine.structured_data import StructuredDataValidator
        validator = StructuredDataValidator()
        schemas = [
            {
                "@context":      "https://schema.org",
                "@type":         "Article",
                "headline":      f"Article {i}",
                "author":        {"@type": "Person", "name": "Author"},
                "datePublished": "2025-07-11",
            }
            for i in range(100)
        ]
        start = time.perf_counter()
        results = [validator.validate(s) for s in schemas]
        elapsed = (time.perf_counter() - start) * 1000
        assert all(r.is_valid for r in results)
        assert elapsed < 500.0, f"100 schema validations took {elapsed:.1f}ms"

    def test_sitemap_generation_1000_urls_under_1s(self):
        """Generate a sitemap with 1000 URLs in under 1 second."""
        from services.seo_engine.sitemap import SitemapGenerator, SitemapURL
        gen = SitemapGenerator()
        urls = [
            SitemapURL(loc=f"https://example.com/page-{i}", priority=0.7)
            for i in range(1000)
        ]
        start = time.perf_counter()
        result = gen.generate(urls)
        elapsed = (time.perf_counter() - start) * 1000
        assert result.url_count == 1000
        assert elapsed < 1000.0, f"1000-URL sitemap took {elapsed:.1f}ms"

    def test_metadata_generation_100_docs_under_500ms(self):
        """Generate metadata for 100 documents in under 500ms."""
        from services.seo_engine.metadata import SEOMetadataOptimiser
        optimiser = SEOMetadataOptimiser()
        content = "Vector database semantic search embeddings HNSW index retrieval. " * 20
        start = time.perf_counter()
        for _ in range(100):
            optimiser.audit(title="Vector Database Guide", meta_description=content[:160])
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 500.0, f"100 metadata audits took {elapsed:.1f}ms"

    def test_duplicate_content_check_50_texts_under_2s(self):
        """Check 50 texts for duplicates in under 2 seconds."""
        from services.seo_engine.validators import SEOValidator
        validator = SEOValidator()
        texts = [f"unique text sample number {i} with extra words here" for i in range(50)]
        start = time.perf_counter()
        results = validator.check_duplicate_content(texts, threshold=0.9)
        elapsed = (time.perf_counter() - start) * 1000
        assert isinstance(results, list)
        assert elapsed < 2000.0, f"50-text duplicate check took {elapsed:.1f}ms"


class TestGEOPerformance:

    def test_entity_extraction_5k_words_under_1s(self):
        """Extract entities from a 5000-word document in under 1 second."""
        from services.geo_engine.entity_mapper import EntityMapper
        mapper = EntityMapper(use_spacy=False)  # rule-based for speed
        content = (
            "OpenAI developed GPT-4 in San Francisco. "
            "Google DeepMind is based in London. "
            "Anthropic created Claude in New York. " * 200
        )
        start = time.perf_counter()
        result = mapper.extract(content, document_id="perf-test")
        elapsed = (time.perf_counter() - start) * 1000
        assert result.entity_count > 0
        assert elapsed < 1000.0, f"Entity extraction (5k words) took {elapsed:.1f}ms"

    def test_citation_analysis_2k_words_under_200ms(self):
        """Analyse citation readiness of 2000-word content in under 200ms."""
        from services.geo_engine.citations import CitationReadinessAnalyser
        analyser = CitationReadinessAnalyser()
        content = (
            "According to MIT (2025), vector search achieves 99.5% recall at k=10. "
            "In Q3 2024, 3.5 million documents were processed per day. "
            'The research team stated "embedding quality determines search precision". ' * 80
        )
        start = time.perf_counter()
        result = analyser.analyse(content)
        elapsed = (time.perf_counter() - start) * 1000
        assert result.overall_score > 0
        assert elapsed < 200.0, f"Citation analysis (2k words) took {elapsed:.1f}ms"

    def test_knowledge_graph_100_entities_under_500ms(self):
        """Build a knowledge graph from 100 entities in under 500ms."""
        from services.geo_engine.knowledge_graph import KnowledgeGraphBuilder
        builder = KnowledgeGraphBuilder()
        content = "OpenAI built GPT-4. " * 50
        entities = [
            {"text": f"Entity{i}", "entity_type": "Organisation",
             "confidence": 0.8, "char_start": i * 10, "char_end": i * 10 + 8}
            for i in range(100)
        ]
        start = time.perf_counter()
        kg = builder.build(content, entities, "perf-doc")
        elapsed = (time.perf_counter() - start) * 1000
        assert len(kg.nodes) == 100
        assert elapsed < 500.0, f"KG build (100 entities) took {elapsed:.1f}ms"

    def test_llm_visibility_scoring_1000_docs_under_2s(self):
        """Score LLM visibility for 1000 documents in under 2 seconds."""
        from services.geo_engine.llm_visibility import LLMVisibilityScorer
        scorer = LLMVisibilityScorer()
        content = "OpenAI achieved 94% accuracy in 2025 using vector embeddings."
        start = time.perf_counter()
        for _ in range(1000):
            scorer.score(content=content, entity_count=5, citation_score=70.0, context_richness=65.0)
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 2000.0, f"1000 LLM visibility scores took {elapsed:.1f}ms"
