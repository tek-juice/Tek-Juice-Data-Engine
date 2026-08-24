"""
DATA ENGINE — GEO Audit Tests
Phase 4: Automated GEO validation tests for entity mapping,
citation readiness, knowledge graph integrity, and LLM visibility scoring.
"""

import pytest
from services.geo_engine.entity_mapper import EntityMapper
from services.geo_engine.citations import CitationReadinessAnalyser
from services.geo_engine.knowledge_graph import KnowledgeGraphBuilder, KGNode, KGEdge
from services.geo_engine.llm_visibility import LLMVisibilityScorer
from services.geo_engine.semantic_optimizer import GEOSemanticOptimiser


class TestEntityMapper:

    def setup_method(self):
        self.mapper = EntityMapper(use_spacy=False)  # use rule-based for tests

    def test_extracts_capitalised_entities(self):
        content = "OpenAI released GPT-4 in San Francisco."
        result = self.mapper.extract(content, document_id="test-doc")
        assert result.entity_count > 0
        labels = [e.text for e in result.entities]
        assert any("OpenAI" in l or "San Francisco" in l for l in labels)

    def test_type_distribution_populated(self):
        content = "Google Inc. is based in Mountain View, California."
        result = self.mapper.extract(content)
        assert isinstance(result.type_distribution, dict)
        assert result.entity_count >= 0

    def test_coverage_score_range(self):
        content = "Anthropic and DeepMind are leading AI research organisations."
        result = self.mapper.extract(content)
        assert 0.0 <= result.coverage_score <= 1.0

    def test_empty_content(self):
        result = self.mapper.extract("")
        assert result.entity_count == 0
        assert result.coverage_score == 0.0

    def test_deduplication(self):
        content = "OpenAI is an AI company. OpenAI was founded in 2015. OpenAI builds GPT."
        result = self.mapper.extract(content)
        labels = [e.text for e in result.entities]
        assert labels.count("OpenAI") <= 1


class TestCitationReadiness:

    def setup_method(self):
        self.analyser = CitationReadinessAnalyser()

    def test_statistics_detected(self):
        content = "The model achieved 94.5% accuracy on the benchmark dataset."
        result = self.analyser.analyse(content)
        assert result.has_statistics
        assert result.statistics_count > 0

    def test_dates_detected(self):
        content = "Released in March 2025, the system processed 1 million documents."
        result = self.analyser.analyse(content)
        assert result.has_dates

    def test_sources_detected(self):
        content = "According to Stanford University, vector search improves recall by 40%."
        result = self.analyser.analyse(content)
        assert result.has_named_sources
        assert len(result.source_references) > 0

    def test_direct_quotes_detected(self):
        content = 'The CEO stated "our system processes 10,000 vectors per second".'
        result = self.analyser.analyse(content)
        assert result.has_direct_quotes

    def test_empty_content_low_score(self):
        result = self.analyser.analyse("")
        assert result.overall_score == 0.0

    def test_recommendations_generated(self):
        result = self.analyser.analyse("A short piece of text with no facts.")
        assert len(result.recommendations) > 0

    def test_rich_content_high_score(self):
        content = (
            'According to MIT, "vector embeddings reduce search latency by 80%". '
            "In Q2 2025, 3.5 million queries were processed using HNSW indexing. "
            "The benchmark showed 99.2% recall at k=10."
        )
        result = self.analyser.analyse(content)
        assert result.overall_score >= 60


class TestKnowledgeGraph:

    def setup_method(self):
        self.builder = KnowledgeGraphBuilder()

    def test_builds_from_entities(self):
        content = "OpenAI is based in San Francisco. They developed GPT-4."
        entities = [
            {"text": "OpenAI",       "entity_type": "Organisation", "confidence": 0.9, "char_start": 0,  "char_end": 6},
            {"text": "San Francisco","entity_type": "Place",        "confidence": 0.9, "char_start": 18, "char_end": 31},
            {"text": "GPT-4",        "entity_type": "Product",      "confidence": 0.8, "char_start": 53, "char_end": 58},
        ]
        kg = self.builder.build(content, entities, "doc-1")
        assert len(kg.nodes) == 3

    def test_co_occurrence_edges_created(self):
        content = "OpenAI developed GPT-4 in 2023."
        entities = [
            {"text": "OpenAI","entity_type": "Organisation","confidence": 0.9,"char_start": 0, "char_end": 6},
            {"text": "GPT-4", "entity_type": "Product",     "confidence": 0.9,"char_start": 17,"char_end": 22},
        ]
        kg = self.builder.build(content, entities)
        assert len(kg.edges) >= 1

    def test_node_deduplication(self):
        content = "OpenAI and OpenAI are both OpenAI."
        entities = [
            {"text": "OpenAI","entity_type": "Organisation","confidence": 0.9,"char_start": i*8,"char_end": i*8+6}
            for i in range(3)
        ]
        kg = self.builder.build(content, entities)
        ids = [n.id for n in kg.nodes]
        assert len(ids) == len(set(ids))

    def test_to_dict_structure(self):
        content = "Acme Corp is a technology company."
        entities = [{"text": "Acme Corp","entity_type": "Organisation","confidence": 0.8,"char_start": 0,"char_end": 9}]
        kg = self.builder.build(content, entities)
        d = kg.to_dict()
        assert "nodes" in d and "edges" in d
        assert d["node_count"] == 1

    def test_to_jsonld_structure(self):
        content = "DeepMind is an AI lab."
        entities = [{"text": "DeepMind","entity_type": "Organisation","confidence": 0.9,"char_start": 0,"char_end": 8}]
        kg = self.builder.build(content, entities)
        jld = kg.to_jsonld()
        assert "@context" in jld
        assert "@graph" in jld


class TestLLMVisibility:

    def setup_method(self):
        self.scorer = LLMVisibilityScorer()

    def test_score_range(self):
        result = self.scorer.score(
            content="Vector databases enable fast semantic search using HNSW indexing.",
            entity_count=5,
            citation_score=60.0,
            context_richness=70.0,
        )
        assert 0.0 <= result.overall_score <= 100.0

    def test_model_scores_present(self):
        result = self.scorer.score(
            content="OpenAI released GPT-4 achieving 96% on MMLU in 2024.",
            entity_count=10,
            citation_score=80.0,
            context_richness=75.0,
            target_models=["gpt-4", "gemini"],
        )
        assert "gpt-4" in result.model_specific_scores
        assert "gemini" in result.model_specific_scores

    def test_improvements_for_weak_content(self):
        result = self.scorer.score(content="Short text.", entity_count=0, citation_score=0.0, context_richness=0.0)
        assert len(result.improvements) > 0

    def test_strengths_for_rich_content(self):
        content = (
            "# Vector Search\n"
            "According to MIT (2025), vector databases reduced query latency by 80%.\n"
            "- HNSW indexing supports 1 billion vectors\n"
            "- Cosine similarity achieves 99.2% recall at k=10\n"
        )
        result = self.scorer.score(
            content=content,
            entity_count=20,
            citation_score=90.0,
            context_richness=85.0,
        )
        assert len(result.strengths) > 0

    def test_structure_score_with_headings_and_lists(self):
        content = "## Introduction\nVector search is fast.\n- Fact one\n- Fact two"
        result = self.scorer.score(content=content, entity_count=2, citation_score=50.0, context_richness=50.0)
        assert result.structure_score > 10


class TestGEOSemanticOptimiser:

    def setup_method(self):
        self.optimiser = GEOSemanticOptimiser()

    def test_returns_optimised_content(self):
        content = "OpenAI builds large language models for various applications."
        entities = [{"text": "OpenAI", "entity_type": "Organisation", "char_start": 0, "char_end": 6}]
        result = self.optimiser.optimise(content, entities)
        assert isinstance(result.optimised_content, str)

    def test_word_count_tracked(self):
        content = "word " * 100
        result = self.optimiser.optimise(content, entities=[])
        assert result.original_word_count == 100

    def test_recommendations_generated_for_short_content(self):
        result = self.optimiser.optimise("Short.", entities=[])
        assert len(result.recommendations) > 0

    def test_context_richness_score_range(self):
        content = (
            "In 2025, OpenAI achieved 95% accuracy on 10,000 test cases. "
            "According to their research, the model processes 1 million tokens per second."
        )
        entities = [{"text": "OpenAI", "entity_type": "Organisation", "char_start": 8, "char_end": 14}]
        result = self.optimiser.optimise(content, entities)
        assert 0.0 <= result.context_richness_score <= 100.0
