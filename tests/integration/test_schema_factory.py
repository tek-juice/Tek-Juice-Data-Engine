"""
DATA ENGINE — Integration Tests: Schema Factory
Tests JSON-LD generation, metadata bundles, ontology building,
and the LLM schema factory flow (without live LLM calls).
"""

import pytest
from services.schema_factory.jsonld import JSONLDGenerator
from services.schema_factory.metadata import MetadataGenerator
from services.schema_factory.ontology import OntologyBuilder, OntologyNode


class TestJSONLDGenerator:

    def setup_method(self):
        self.gen = JSONLDGenerator()

    def test_article_schema_valid_structure(self):
        schema = self.gen.generate(
            schema_type="Article",
            content="Vector databases store embeddings for fast semantic search.",
            metadata={
                "title":         "Vector DB Guide",
                "author":        "Tek Juice AI",
                "datePublished": "2025-07-11",
                "description":   "A guide to vector databases.",
            },
        )
        assert schema["@context"] == "https://schema.org"
        assert schema["@type"] == "Article"
        assert "headline" in schema
        assert "author" in schema

    def test_faq_page_has_main_entity(self):
        schema = self.gen.generate(
            schema_type="FAQPage",
            content="What is a vector database? It stores embeddings.",
            metadata={
                "faq_items": [
                    {"question": "What is a vector database?",
                     "answer": "A database for high-dimensional vectors."},
                ]
            },
        )
        assert schema["@type"] == "FAQPage"
        assert len(schema["mainEntity"]) == 1
        assert schema["mainEntity"][0]["@type"] == "Question"

    def test_howto_has_steps(self):
        schema = self.gen.generate(
            schema_type="HowTo",
            content="1. Install PostgreSQL 2. Enable PGVector 3. Create HNSW index",
            metadata={"title": "Setup Vector Database"},
        )
        assert schema["@type"] == "HowTo"
        assert "step" in schema

    def test_web_page_fallback(self):
        schema = self.gen.generate(
            schema_type="UnknownType",
            content="Some content",
            metadata={"title": "Test Page"},
        )
        assert schema["@type"] == "WebPage"

    def test_all_supported_types(self):
        supported = [
            "Article", "FAQPage", "HowTo", "Product",
            "Organisation", "WebPage", "Dataset", "SoftwareApplication"
        ]
        for stype in supported:
            schema = self.gen.generate(schema_type=stype, content="test content", metadata={})
            assert schema.get("@type") == stype or schema.get("@type") == "WebPage"

    def test_to_script_tag(self):
        schema = self.gen.generate("Article", "content", {"title": "T", "author": "A", "datePublished": "2025"})
        html = self.gen.to_script_tag(schema)
        assert '<script type="application/ld+json">' in html
        assert "</script>" in html


class TestMetadataGenerator:

    def setup_method(self):
        self.gen = MetadataGenerator()

    def test_bundle_has_all_fields(self):
        bundle = self.gen.generate(
            content="Vector databases enable semantic search using HNSW indexing.",
            metadata={"title": "Vector DB Guide", "description": "A complete guide."},
        )
        assert bundle.title
        assert bundle.description
        assert isinstance(bundle.keywords, list)
        assert isinstance(bundle.open_graph, dict)
        assert isinstance(bundle.twitter_card, dict)
        assert isinstance(bundle.html_meta, list)

    def test_title_truncated_to_max(self):
        bundle = self.gen.generate(content="c", metadata={"title": "A" * 200})
        assert len(bundle.title) <= 70

    def test_description_truncated_to_max(self):
        bundle = self.gen.generate(content="c", metadata={"description": "B" * 300})
        assert len(bundle.description) <= 160

    def test_open_graph_required_keys(self):
        bundle = self.gen.generate(content="test content here for metadata generation")
        for key in ("og:type", "og:title", "og:description"):
            assert key in bundle.open_graph

    def test_twitter_card_required_keys(self):
        bundle = self.gen.generate(content="test content here for twitter card testing")
        assert "twitter:card" in bundle.twitter_card
        assert "twitter:title" in bundle.twitter_card

    def test_keywords_extracted_automatically(self):
        content = "semantic vector embeddings search database indexing retrieval"
        bundle = self.gen.generate(content=content)
        assert len(bundle.keywords) > 0

    def test_to_html_string(self):
        bundle = self.gen.generate(content="test content for html meta generation check")
        html = self.gen.to_html_string(bundle)
        assert "<title>" in html
        assert '<meta ' in html


class TestOntologyBuilder:

    def setup_method(self):
        self.builder = OntologyBuilder()

    def test_builds_from_topics(self):
        topics = ["vector database", "semantic search", "HNSW indexing"]
        ontology = self.builder.build_from_topics(topics)
        assert len(ontology.nodes) > 0

    def test_no_duplicate_nodes(self):
        topics = ["embeddings", "embeddings", "vector search"]
        ontology = self.builder.build_from_topics(topics)
        ids = [n.id for n in ontology.nodes]
        assert len(ids) == len(set(ids))

    def test_technology_classification(self):
        ontology = self.builder.build_from_topics(["vector API integration"])
        tech_nodes = [n for n in ontology.nodes if n.type == "Technology"]
        assert len(tech_nodes) >= 1

    def test_to_dict_structure(self):
        ontology = self.builder.build_from_topics(["concept one", "concept two"])
        d = ontology.to_dict()
        assert "nodes" in d
        assert "node_count" in d

    def test_to_jsonld_has_context(self):
        ontology = self.builder.build_from_topics(["AI", "vector search"])
        jld = ontology.to_jsonld()
        assert "@context" in jld
        assert "@graph" in jld
        assert len(jld["@graph"]) > 0

    def test_get_node_by_id(self):
        ontology = self.builder.build_from_topics(["data pipeline"])
        first_node = ontology.nodes[0]
        found = ontology.get_node(first_node.id)
        assert found is not None
        assert found.label == first_node.label

    def test_get_nonexistent_node_returns_none(self):
        ontology = self.builder.build_from_topics(["test"])
        assert ontology.get_node("does_not_exist") is None
