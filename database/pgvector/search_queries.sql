-- DATA ENGINE — PGVector Search Query Templates
-- IMPORTANT: Always filter by model/dimensions before doing <=> distance.
-- Cross-model vector comparisons produce meaningless scores and are forbidden.
-- Each query selects the correct namespace column based on the embedding model.

-- ── Helper: resolve namespace column by dimension
-- Use these in application code to select the right column:
--   768  → embedding_768   (Gemini text-embedding-004, Jina v2)
--   1536 → embedding_1536  (OpenAI text-embedding-3-small, Voyage large-2)
--   1024 → embedding_1024  (Voyage-2)
--   3072 → embedding_3072  (OpenAI text-embedding-3-large)


-- ── Cosine Similarity Search (768-dim — Gemini / Jina)
-- $1 = query_vector::vector(768)
-- $2 = tenant_id::uuid
-- $3 = similarity_threshold::float
-- $4 = top_k::int
SELECT
    e.id             AS embedding_id,
    e.chunk_id,
    c.text,
    c.chunk_index,
    c.metadata       AS chunk_metadata,
    e.provider,
    e.model,
    e.dimensions,
    1 - (e.embedding_768 <=> $1::vector(768)) AS similarity
FROM embeddings e
JOIN chunks c ON c.id = e.chunk_id
WHERE
    e.tenant_id  = $2::uuid
    AND e.dimensions = 768
    AND e.embedding_768 IS NOT NULL
    AND 1 - (e.embedding_768 <=> $1::vector(768)) >= $3
ORDER BY e.embedding_768 <=> $1::vector(768)
LIMIT $4;


-- ── Cosine Similarity Search (1536-dim — OpenAI / Voyage large)
-- $1 = query_vector::vector(1536)
-- $2 = tenant_id::uuid
-- $3 = similarity_threshold::float
-- $4 = top_k::int
SELECT
    e.id             AS embedding_id,
    e.chunk_id,
    c.text,
    c.chunk_index,
    c.metadata       AS chunk_metadata,
    e.provider,
    e.model,
    e.dimensions,
    1 - (e.embedding_1536 <=> $1::vector(1536)) AS similarity
FROM embeddings e
JOIN chunks c ON c.id = e.chunk_id
WHERE
    e.tenant_id  = $2::uuid
    AND e.dimensions = 1536
    AND e.embedding_1536 IS NOT NULL
    AND 1 - (e.embedding_1536 <=> $1::vector(1536)) >= $3
ORDER BY e.embedding_1536 <=> $1::vector(1536)
LIMIT $4;


-- ── Filtered Search by document (768-dim)
SELECT
    e.chunk_id,
    c.text,
    e.model,
    1 - (e.embedding_768 <=> $1::vector(768)) AS similarity
FROM embeddings e
JOIN chunks c ON c.id = e.chunk_id
WHERE
    e.tenant_id    = $2::uuid
    AND e.document_id = $3::uuid
    AND e.dimensions  = 768
    AND 1 - (e.embedding_768 <=> $1::vector(768)) >= $4
ORDER BY e.embedding_768 <=> $1::vector(768)
LIMIT $5;


-- ── Gap Detection: Find uncovered topics (768-dim)
-- Identify trend embeddings NOT well-represented in a document's embeddings.
-- Both sides must use the same model — enforced by dimensions filter.
SELECT
    t.id,
    t.title,
    t.snippet,
    t.source,
    t.embedding_model,
    MIN(e.embedding_768 <=> t.embedding_768) AS min_distance
FROM scraped_trends t
CROSS JOIN LATERAL (
    SELECT embedding_768
    FROM embeddings
    WHERE
        document_id = $1::uuid
        AND tenant_id = $2::uuid
        AND dimensions = 768
        AND embedding_768 IS NOT NULL
    ORDER BY embedding_768 <=> t.embedding_768
    LIMIT 1
) e
WHERE
    t.scraped_at >= NOW() - INTERVAL '7 days'
    AND t.embedding_768 IS NOT NULL
    AND t.embedding_dims = 768
GROUP BY t.id, t.title, t.snippet, t.source, t.embedding_model
HAVING MIN(e.embedding_768 <=> t.embedding_768) > $3
ORDER BY min_distance DESC
LIMIT 20;


-- ── Entity Similarity Search / GEO Engine (768-dim)
SELECT
    ge.id,
    ge.entity_text,
    ge.entity_type,
    ge.confidence,
    ge.wikidata_id,
    ge.same_as_urls,
    ge.embedding_model,
    1 - (ge.embedding_768 <=> $1::vector(768)) AS similarity
FROM geo_entities ge
WHERE
    ge.tenant_id     = $2::uuid
    AND ge.entity_type   = ANY($3::text[])
    AND ge.embedding_dims = 768
    AND ge.embedding_768  IS NOT NULL
ORDER BY ge.embedding_768 <=> $1::vector(768)
LIMIT $4;


-- ── Duplicate Detection (768-dim)
SELECT
    a.id AS chunk_a,
    b.id AS chunk_b,
    a.document_id AS doc_a,
    b.document_id AS doc_b,
    1 - (ae.embedding_768 <=> be.embedding_768) AS similarity
FROM embeddings ae
JOIN embeddings be
    ON ae.tenant_id  = be.tenant_id
    AND ae.id        < be.id
    AND ae.dimensions = 768
    AND be.dimensions = 768
    AND 1 - (ae.embedding_768 <=> be.embedding_768) > 0.98
JOIN chunks a ON a.id = ae.chunk_id
JOIN chunks b ON b.id = be.chunk_id
WHERE ae.tenant_id = $1::uuid
LIMIT 100;


-- ── Cross-Document Semantic Comparison (768-dim)
WITH doc_a_avg AS (
    SELECT AVG(embedding_768) AS avg_vec
    FROM embeddings
    WHERE document_id = $1::uuid AND tenant_id = $3::uuid AND dimensions = 768
),
doc_b_avg AS (
    SELECT AVG(embedding_768) AS avg_vec
    FROM embeddings
    WHERE document_id = $2::uuid AND tenant_id = $3::uuid AND dimensions = 768
)
SELECT
    1 - (a.avg_vec <=> b.avg_vec) AS document_similarity
FROM doc_a_avg a, doc_b_avg b;
