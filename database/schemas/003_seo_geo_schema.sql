-- DATA ENGINE — SEO & GEO Schema
-- Phase 4: Tables for SEO analysis, GEO entity mapping, knowledge graph

-- SEO Analysis
CREATE TABLE IF NOT EXISTS seo_analysis (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    target_keywords     TEXT[] DEFAULT '{}',
    matched_keywords    TEXT[] DEFAULT '{}',
    keyword_density     JSONB DEFAULT '{}',
    title_score         FLOAT,
    meta_description    TEXT,
    readability_score   FLOAT,
    schema_valid        BOOLEAN,
    issues              TEXT[] DEFAULT '{}',
    recommendations     TEXT[] DEFAULT '{}',
    overall_score       FLOAT CHECK (overall_score BETWEEN 0.0 AND 100.0),
    metadata            JSONB NOT NULL DEFAULT '{}',
    analysed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_seo_document_id ON seo_analysis(document_id);
CREATE INDEX IF NOT EXISTS idx_seo_tenant_id   ON seo_analysis(tenant_id);

-- ── GEO Entity Map
CREATE TABLE IF NOT EXISTS geo_entities (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    entity_text     TEXT NOT NULL,
    entity_type     TEXT NOT NULL
                        CHECK (entity_type IN (
                            'Person','Organisation','Place',
                            'Product','Event','Concept','Technology'
                        )),
    confidence      FLOAT CHECK (confidence BETWEEN 0.0 AND 1.0),
    -- Namespace columns
    embedding_768   VECTOR(768),
    embedding_1536  VECTOR(1536),
    embedding_model TEXT,
    embedding_dims  INTEGER,
    wikidata_id     TEXT,
    same_as_urls    TEXT[] DEFAULT '{}',   -- sameAs links for GEO entity trust
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_geo_entities_document_id  ON geo_entities(document_id);
CREATE INDEX IF NOT EXISTS idx_geo_entities_tenant_id    ON geo_entities(tenant_id);
CREATE INDEX IF NOT EXISTS idx_geo_entities_type         ON geo_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_geo_entities_hnsw_768     ON geo_entities USING hnsw (embedding_768  vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE embedding_768  IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_geo_entities_hnsw_1536    ON geo_entities USING hnsw (embedding_1536 vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE embedding_1536 IS NOT NULL;

-- ── Knowledge Graph Edges 
CREATE TABLE IF NOT EXISTS knowledge_graph_edges (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source_id       UUID NOT NULL REFERENCES geo_entities(id) ON DELETE CASCADE,
    target_id       UUID NOT NULL REFERENCES geo_entities(id) ON DELETE CASCADE,
    relationship    TEXT NOT NULL,
    weight          FLOAT DEFAULT 1.0,
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kg_edges_source    ON knowledge_graph_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_target    ON knowledge_graph_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_tenant_id ON knowledge_graph_edges(tenant_id);

-- ── GEO Optimisation Results 
CREATE TABLE IF NOT EXISTS geo_optimisation_results (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id             UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    llm_visibility_score    FLOAT CHECK (llm_visibility_score BETWEEN 0.0 AND 100.0),
    entity_coverage         FLOAT,
    citation_readiness      BOOLEAN DEFAULT FALSE,
    context_richness_score  FLOAT,
    optimised_content       TEXT,
    target_models           TEXT[] DEFAULT '{}',
    recommendations         TEXT[] DEFAULT '{}',
    metadata                JSONB NOT NULL DEFAULT '{}',
    optimised_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_geo_opt_document_id ON geo_optimisation_results(document_id);
CREATE INDEX IF NOT EXISTS idx_geo_opt_tenant_id   ON geo_optimisation_results(tenant_id);
