"""
DATA ENGINE — Integration Tests: Full Pipeline
Phase 4: End-to-end pipeline integration tests.
These require a running PostgreSQL + Redis instance (use Docker Compose).

Run with:
    pytest services/testing_service/integration_tests/test_pipeline.py -v -m integration
"""

import pytest


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_chunking_to_embedding_pipeline():
    """
    Integration: chunk a document then generate embeddings.
    Validates that chunk output feeds correctly into the embedding pipeline.
    """
    from services.chunking_service.token_chunker import TokenChunker
    from services.embedding_service.embedding_pipeline import EmbeddingPipeline

    text = (
        "Vector databases store high-dimensional embeddings produced by neural network models. "
        "These embeddings capture semantic relationships between pieces of text, allowing for "
        "similarity search at scale. The HNSW algorithm provides approximate nearest-neighbour "
        "search with sub-linear query time and high recall. " * 5
    )
    chunker = TokenChunker(min_tokens=64, max_tokens=128, overlap_tokens=16)
    chunks = chunker.chunk(text)
    assert len(chunks) >= 2

    texts = [c.text for c in chunks]
    pipeline = EmbeddingPipeline()
    embeddings, provider, model = await pipeline.embed(texts[:2])

    assert len(embeddings) == 2
    assert all(len(e) > 0 for e in embeddings)
    assert provider in ("openai", "gemini", "voyage", "jina")


@pytest.mark.asyncio
async def test_semantic_comparison_pipeline():
    """
    Integration: compare two documents using cosine similarity.
    Validates that the semantic engine works with real vector data.
    """
    from services.semantic_engine.cosine_similarity import cosine_similarity
    from services.semantic_engine.comparator import SemanticComparator

    vec_a = [0.1, 0.2, 0.3, 0.4, 0.5]
    vec_b = [0.1, 0.2, 0.3, 0.4, 0.5]
    vec_c = [-0.5, -0.4, -0.3, -0.2, -0.1]

    sim_identical = cosine_similarity(vec_a, vec_b)
    sim_opposite = cosine_similarity(vec_a, vec_c)

    assert abs(sim_identical - 1.0) < 0.0001
    assert sim_opposite < 0.0

    comparator = SemanticComparator()
    result = comparator.compare_document_sets(
        "doc-a", [vec_a], "doc-b", [vec_b]
    )
    assert result.similarity > 0.99


@pytest.mark.asyncio
async def test_gap_detection_scoring_pipeline():
    """
    Integration: gap detection scoring with real vector math.
    """
    from services.gap_detection.scoring import compute_gap_score, classify_severity
    from configs.constants import GapSeverity

    score_high = compute_gap_score(
        uncovered_count=80, total_trends=100, avg_max_similarity=0.3
    )
    score_low = compute_gap_score(
        uncovered_count=5, total_trends=100, avg_max_similarity=0.85
    )

    assert score_high > score_low
    severity_high = classify_severity(score_high)
    severity_low = classify_severity(score_low)
    assert severity_high in (GapSeverity.HIGH, GapSeverity.CRITICAL)
    assert severity_low in (GapSeverity.LOW, GapSeverity.MEDIUM)


@pytest.mark.asyncio
async def test_schema_generation_pipeline():
    """
    Integration: schema factory generates valid JSON-LD from content.
    """
    from services.schema_factory.jsonld import JSONLDGenerator
    from services.schema_factory.metadata import MetadataGenerator
    from services.seo_engine.structured_data import StructuredDataValidator

    content = "Vector databases store embeddings for fast semantic search at scale."
    gen = JSONLDGenerator()
    schema = gen.generate(
        schema_type="Article",
        content=content,
        metadata={
            "title":         "Vector Database Guide",
            "author":        "DATA ENGINE",
            "datePublished": "2025-07-11",
        },
    )

    assert schema.get("@type") == "Article"
    assert schema.get("@context") == "https://schema.org"

    validator = StructuredDataValidator()
    result = validator.validate(schema)
    assert result.is_valid, f"Schema invalid: {result.errors}"

    meta_gen = MetadataGenerator()
    meta = meta_gen.generate(content=content, metadata={"title": "Vector Database Guide"})
    assert meta.title
    assert meta.description
    assert len(meta.open_graph) > 0


@pytest.mark.asyncio
async def test_seo_full_audit_pipeline():
    """
    Integration: full SEO audit — keywords + metadata + structured data.
    """
    from services.seo_engine.keyword_analysis import KeywordAnalyser
    from services.seo_engine.metadata import SEOMetadataOptimiser

    content = (
        "Vector databases enable semantic search by storing high-dimensional embeddings. "
        "This guide covers HNSW indexing, cosine similarity, and PGVector configuration."
    )
    keywords = ["vector databases", "semantic search", "HNSW indexing"]

    analyser = KeywordAnalyser()
    kw_result = analyser.analyse(content, keywords)
    assert kw_result.coverage_score > 0.0

    optimiser = SEOMetadataOptimiser()
    meta_result = optimiser.audit(
        title="Vector Database Complete Guide",
        meta_description="Learn how vector databases power semantic search with embeddings.",
        primary_keyword="vector databases",
    )
    assert meta_result.score >= 50
