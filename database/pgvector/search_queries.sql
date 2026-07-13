-- DATA ENGINE — PGVector Search Query Templates
-- Reference queries for the Vector Vault and Semantic Engine.

-- ── Basic Cosine Similarity Search 
-- Returns top-k most similar chunks for a given query embedding.
SELECT
    e.id             AS embedding_id,
    e.chunk_id,
    c.text,
    c.chunk_index,
    c.metadata       AS chunk_metadata,
    e.provider,
    e.model,
    1 - (e.embedding <=> $1::vector) AS similarity
FROM embeddings e
JOIN chunks c ON c.id = e.chunk_id
WHERE
    e.tenant_id = $2::uuid
    AND 1 - (e.embedding <=> $1::vector) >= $3   -- similarity threshold
ORDER BY e.embedding <=> $1::vector               -- cosine distance (ascending)
LIMIT $4;                                          -- top_k


-- ── Filtered Search (by document) 
SELECT
    e.chunk_id,
    c.text,
    1 - (e.embedding <=> $1::vector) AS similarity
FROM embeddings e
JOIN chunks c ON c.id = e.chunk_id
WHERE
    e.tenant_id    = $2::uuid
    AND e.document_id = $3::uuid
    AND 1 - (e.embedding <=> $1::vector) >= $4
ORDER BY e.embedding <=> $1::vector
LIMIT $5;


-- ── Cross-Document Semantic Comparison 
-- Compare two documents by their average embeddings.
WITH doc_a_avg AS (
    SELECT AVG(embedding) AS avg_vec
    FROM embeddings
    WHERE document_id = $1::uuid AND tenant_id = $3::uuid
),
doc_b_avg AS (
    SELECT AVG(embedding) AS avg_vec
    FROM embeddings
    WHERE document_id = $2::uuid AND tenant_id = $3::uuid
)
SELECT
    1 - (a.avg_vec <=> b.avg_vec) AS document_similarity
FROM doc_a_avg a, doc_b_avg b;


-- ── Gap Detection: Find uncovered topics 
-- Identify trend embeddings that are NOT well-represented in a document's embeddings.
SELECT
    t.id,
    t.title,
    t.snippet,
    t.source,
    MIN(e.embedding <=> t.embedding) AS min_distance
FROM scraped_trends t
CROSS JOIN LATERAL (
    SELECT embedding
    FROM embeddings
    WHERE document_id = $1::uuid AND tenant_id = $2::uuid
    ORDER BY embedding <=> t.embedding
    LIMIT 1
) e
WHERE t.scraped_at >= NOW() - INTERVAL '7 days'
GROUP BY t.id, t.title, t.snippet, t.source
HAVING MIN(e.embedding <=> t.embedding) > $3   -- gap_threshold (e.g. 0.40)
ORDER BY min_distance DESC
LIMIT 20;


-- ── Entity Similarity Search (GEO Engine) 
SELECT
    ge.id,
    ge.entity_text,
    ge.entity_type,
    ge.confidence,
    1 - (ge.embedding <=> $1::vector) AS similarity
FROM geo_entities ge
WHERE
    ge.tenant_id = $2::uuid
    AND ge.entity_type = ANY($3::text[])   -- filter by entity types
ORDER BY ge.embedding <=> $1::vector
LIMIT $4;


-- ── Duplicate Detection 
-- Find near-duplicate chunks within a tenant (similarity > 0.98).
SELECT
    a.id AS chunk_a,
    b.id AS chunk_b,
    a.document_id AS doc_a,
    b.document_id AS doc_b,
    1 - (ae.embedding <=> be.embedding) AS similarity
FROM embeddings ae
JOIN embeddings be
    ON ae.tenant_id = be.tenant_id
    AND ae.id < be.id
    AND 1 - (ae.embedding <=> be.embedding) > 0.98
JOIN chunks a ON a.id = ae.chunk_id
JOIN chunks b ON b.id = be.chunk_id
WHERE ae.tenant_id = $1::uuid
LIMIT 100;
