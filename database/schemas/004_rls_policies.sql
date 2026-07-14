-- DATA ENGINE — Row-Level Security Policies
-- Phase 4: Tenant isolation via PostgreSQL RLS
-- Ensures each tenant can only access their own data.
-- Set the tenant context before each request:
--   SET LOCAL app.current_tenant_id = '<tenant_uuid>';

Enable RLS on all tenant-scoped tables 
ALTER TABLE users                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents               ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE embeddings              ENABLE ROW LEVEL SECURITY;
ALTER TABLE telemetry_events        ENABLE ROW LEVEL SECURITY;
ALTER TABLE gap_analysis_results    ENABLE ROW LEVEL SECURITY;
ALTER TABLE generated_schemas       ENABLE ROW LEVEL SECURITY;
ALTER TABLE seo_analysis            ENABLE ROW LEVEL SECURITY;
ALTER TABLE geo_entities            ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_graph_edges   ENABLE ROW LEVEL SECURITY;
ALTER TABLE geo_optimisation_results ENABLE ROW LEVEL SECURITY;

 Policies 

-- users
DROP POLICY IF EXISTS tenant_isolation ON users;
CREATE POLICY tenant_isolation ON users
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- documents
DROP POLICY IF EXISTS tenant_isolation ON documents;
CREATE POLICY tenant_isolation ON documents
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- chunks
DROP POLICY IF EXISTS tenant_isolation ON chunks;
CREATE POLICY tenant_isolation ON chunks
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- embeddings
DROP POLICY IF EXISTS tenant_isolation ON embeddings;
CREATE POLICY tenant_isolation ON embeddings
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- telemetry_events
DROP POLICY IF EXISTS tenant_isolation ON telemetry_events;
CREATE POLICY tenant_isolation ON telemetry_events
    USING (
        tenant_id IS NULL
        OR tenant_id = current_setting('app.current_tenant_id', true)::uuid
    );

-- gap_analysis_results
DROP POLICY IF EXISTS tenant_isolation ON gap_analysis_results;
CREATE POLICY tenant_isolation ON gap_analysis_results
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- generated_schemas
DROP POLICY IF EXISTS tenant_isolation ON generated_schemas;
CREATE POLICY tenant_isolation ON generated_schemas
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- seo_analysis
DROP POLICY IF EXISTS tenant_isolation ON seo_analysis;
CREATE POLICY tenant_isolation ON seo_analysis
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- geo_entities
DROP POLICY IF EXISTS tenant_isolation ON geo_entities;
CREATE POLICY tenant_isolation ON geo_entities
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- knowledge_graph_edges
DROP POLICY IF EXISTS tenant_isolation ON knowledge_graph_edges;
CREATE POLICY tenant_isolation ON knowledge_graph_edges
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- geo_optimisation_results
DROP POLICY IF EXISTS tenant_isolation ON geo_optimisation_results;
CREATE POLICY tenant_isolation ON geo_optimisation_results
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- ── Service role bypasses RLS (internal service account) ─────────────────────
-- The service DB user uses BYPASSRLS to allow admin operations.
-- Application user (data_engine_app) is subject to all policies above.
-- ALTER ROLE data_engine_service BYPASSRLS;
