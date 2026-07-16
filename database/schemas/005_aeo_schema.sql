-- DATA ENGINE — AEO Schema (Migration 005)
-- Answer Engine Optimisation tables.
-- Sits alongside 003_seo_geo_schema.sql — same pattern, same conventions.
-- Stores AEO analysis results, question maps, snippet candidates,
-- voice search scores, and position-zero opportunity logs.

-- ── AEO Optimisation Results (master results table) ───────────────────────────
CREATE TABLE IF NOT EXISTS aeo_optimisation_results (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    -- Composite scores (0–100)
    overall_aeo_score   FLOAT CHECK (overall_aeo_score  BETWEEN 0.0 AND 100.0),
    answer_readiness    FLOAT CHECK (answer_readiness   BETWEEN 0.0 AND 100.0),
    voice_readiness     FLOAT CHECK (voice_readiness    BETWEEN 0.0 AND 100.0),
    snippet_readiness   FLOAT CHECK (snippet_readiness  BETWEEN 0.0 AND 100.0),
    pz_readiness        FLOAT CHECK (pz_readiness       BETWEEN 0.0 AND 100.0),

    -- Snippet & voice outputs
    snippet_ready       BOOLEAN NOT NULL DEFAULT FALSE,
    voice_answer        TEXT,                    -- 20–30 word spoken response
    optimised_intro     TEXT,                    -- rewritten intro paragraph

    -- Question coverage
    questions_mapped    INTEGER DEFAULT 0,
    questions_answered  INTEGER DEFAULT 0,

    -- Position-zero
    pz_opportunities    INTEGER DEFAULT 0,

    -- Action plan (stored as JSON arrays)
    priority_actions    JSONB NOT NULL DEFAULT '[]',
    quick_wins          JSONB NOT NULL DEFAULT '[]',
    recommendations     JSONB NOT NULL DEFAULT '[]',

    -- Engine targets
    engine_targets      JSONB NOT NULL DEFAULT '[]',

    -- Flexible metadata
    metadata            JSONB NOT NULL DEFAULT '{}',

    analysed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_aeo_results_document_id  ON aeo_optimisation_results(document_id);
CREATE INDEX IF NOT EXISTS idx_aeo_results_tenant_id    ON aeo_optimisation_results(tenant_id);
CREATE INDEX IF NOT EXISTS idx_aeo_results_analysed_at  ON aeo_optimisation_results(analysed_at DESC);
CREATE INDEX IF NOT EXISTS idx_aeo_results_score        ON aeo_optimisation_results(overall_aeo_score DESC);


-- ── AEO Question Map (one row per detected question) ──────────────────────────
CREATE TABLE IF NOT EXISTS aeo_questions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aeo_result_id       UUID NOT NULL REFERENCES aeo_optimisation_results(id) ON DELETE CASCADE,
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    question_text       TEXT NOT NULL,
    question_type       TEXT NOT NULL
                            CHECK (question_type IN ('explicit', 'implicit')),
    intent              TEXT NOT NULL
                            CHECK (intent IN (
                                'informational', 'navigational', 'transactional',
                                'comparative', 'procedural', 'definitional',
                                'causal', 'quantitative'
                            )),
    is_voice_search     BOOLEAN NOT NULL DEFAULT FALSE,
    is_long_tail        BOOLEAN NOT NULL DEFAULT FALSE,
    word_count          INTEGER,
    has_direct_answer   BOOLEAN NOT NULL DEFAULT FALSE,
    answer_excerpt      TEXT,
    answer_quality      FLOAT CHECK (answer_quality BETWEEN 0.0 AND 1.0),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_aeo_questions_result_id  ON aeo_questions(aeo_result_id);
CREATE INDEX IF NOT EXISTS idx_aeo_questions_document   ON aeo_questions(document_id);
CREATE INDEX IF NOT EXISTS idx_aeo_questions_intent     ON aeo_questions(intent);
CREATE INDEX IF NOT EXISTS idx_aeo_questions_unanswered ON aeo_questions(document_id)
    WHERE has_direct_answer = FALSE;


-- ── AEO Featured Snippet Candidates (one row per candidate block) ─────────────
CREATE TABLE IF NOT EXISTS aeo_snippet_candidates (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aeo_result_id           UUID NOT NULL REFERENCES aeo_optimisation_results(id) ON DELETE CASCADE,
    document_id             UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    snippet_type            TEXT NOT NULL
                                CHECK (snippet_type IN (
                                    'paragraph', 'list', 'table',
                                    'steps', 'definition', 'none'
                                )),
    content_block           TEXT NOT NULL,
    optimised_text          TEXT,
    word_count              INTEGER,
    snippet_score           FLOAT CHECK (snippet_score BETWEEN 0.0 AND 100.0),
    readability_score       FLOAT,
    starts_with_direct_answer BOOLEAN DEFAULT FALSE,
    has_trigger_phrase      BOOLEAN DEFAULT FALSE,
    trigger_match           TEXT,
    is_best_candidate       BOOLEAN NOT NULL DEFAULT FALSE,

    issues                  TEXT[]  DEFAULT '{}',

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_aeo_snippets_result_id   ON aeo_snippet_candidates(aeo_result_id);
CREATE INDEX IF NOT EXISTS idx_aeo_snippets_document    ON aeo_snippet_candidates(document_id);
CREATE INDEX IF NOT EXISTS idx_aeo_snippets_type        ON aeo_snippet_candidates(snippet_type);
CREATE INDEX IF NOT EXISTS idx_aeo_snippets_best        ON aeo_snippet_candidates(document_id)
    WHERE is_best_candidate = TRUE;


-- ── AEO Position-Zero Opportunities (one row per opportunity) ─────────────────
CREATE TABLE IF NOT EXISTS aeo_position_zero (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aeo_result_id       UUID NOT NULL REFERENCES aeo_optimisation_results(id) ON DELETE CASCADE,
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    position_zero_type  TEXT NOT NULL
                            CHECK (position_zero_type IN (
                                'featured_snippet_paragraph',
                                'featured_snippet_list',
                                'featured_snippet_table',
                                'featured_snippet_steps',
                                'people_also_ask',
                                'knowledge_panel',
                                'answer_box',
                                'ai_overview',
                                'none'
                            )),
    content_block       TEXT NOT NULL,
    readiness_score     FLOAT CHECK (readiness_score BETWEEN 0.0 AND 100.0),
    required_changes    TEXT[]  DEFAULT '{}',
    trigger_found       TEXT,
    is_best_opportunity BOOLEAN NOT NULL DEFAULT FALSE,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_aeo_pz_result_id     ON aeo_position_zero(aeo_result_id);
CREATE INDEX IF NOT EXISTS idx_aeo_pz_document      ON aeo_position_zero(document_id);
CREATE INDEX IF NOT EXISTS idx_aeo_pz_type          ON aeo_position_zero(position_zero_type);
CREATE INDEX IF NOT EXISTS idx_aeo_pz_readiness     ON aeo_position_zero(readiness_score DESC);
CREATE INDEX IF NOT EXISTS idx_aeo_pz_best          ON aeo_position_zero(document_id)
    WHERE is_best_opportunity = TRUE;


-- ── AEO Voice Search Results (one row per analysis) ───────────────────────────
CREATE TABLE IF NOT EXISTS aeo_voice_results (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aeo_result_id               UUID NOT NULL REFERENCES aeo_optimisation_results(id) ON DELETE CASCADE,
    document_id                 UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id                   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    overall_score               FLOAT CHECK (overall_score BETWEEN 0.0 AND 100.0),
    conversational_score        FLOAT,
    answer_length_score         FLOAT,
    question_alignment_score    FLOAT,
    local_intent_score          FLOAT,

    word_count                  INTEGER,
    is_conversational           BOOLEAN DEFAULT FALSE,
    has_local_intent            BOOLEAN DEFAULT FALSE,
    filler_words_found          TEXT[]  DEFAULT '{}',
    detected_voice_patterns     TEXT[]  DEFAULT '{}',

    spoken_answer               TEXT,       -- 20–30 word extracted answer
    optimised_for_voice         TEXT,       -- cleaned spoken version

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_aeo_voice_result_id  ON aeo_voice_results(aeo_result_id);
CREATE INDEX IF NOT EXISTS idx_aeo_voice_document   ON aeo_voice_results(document_id);
CREATE INDEX IF NOT EXISTS idx_aeo_voice_score      ON aeo_voice_results(overall_score DESC);


-- ── AEO Answer Score Log (one row per scored content block) ───────────────────
CREATE TABLE IF NOT EXISTS aeo_answer_scores (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aeo_result_id           UUID REFERENCES aeo_optimisation_results(id) ON DELETE CASCADE,
    document_id             UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    overall_score           FLOAT CHECK (overall_score BETWEEN 0.0 AND 100.0),
    directness_score        FLOAT,
    completeness_score      FLOAT,
    conciseness_score       FLOAT,
    verifiability_score     FLOAT,

    answer_type             TEXT CHECK (answer_type IN (
                                'paragraph', 'list', 'table',
                                'steps', 'definition'
                            )),
    word_count              INTEGER,
    starts_directly         BOOLEAN DEFAULT FALSE,
    has_filler_opener       BOOLEAN DEFAULT FALSE,
    snippet_ready           BOOLEAN DEFAULT FALSE,
    has_statistics          BOOLEAN DEFAULT FALSE,
    has_named_sources       BOOLEAN DEFAULT FALSE,
    completeness_signals    TEXT[]  DEFAULT '{}',

    content_block           TEXT,
    issues                  TEXT[]  DEFAULT '{}',
    recommendations         TEXT[]  DEFAULT '{}',

    scored_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_aeo_answers_result_id    ON aeo_answer_scores(aeo_result_id);
CREATE INDEX IF NOT EXISTS idx_aeo_answers_document     ON aeo_answer_scores(document_id);
CREATE INDEX IF NOT EXISTS idx_aeo_answers_score        ON aeo_answer_scores(overall_score DESC);
CREATE INDEX IF NOT EXISTS idx_aeo_answers_snippet_ready ON aeo_answer_scores(document_id)
    WHERE snippet_ready = TRUE;
