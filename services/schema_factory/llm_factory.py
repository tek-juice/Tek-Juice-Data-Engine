"""
DATA ENGINE — LLM Schema Factory
Phase 2: Uses an LLM to generate structured schemas, metadata expansions,
and ontology relationships when gaps are detected.
"""

import json
import structlog
from typing import Any

from configs.settings import get_settings
from services.schema_factory.jsonld import JSONLDGenerator
from services.schema_factory.metadata import MetadataGenerator
from services.schema_factory.ontology import OntologyBuilder

logger = structlog.get_logger(__name__)
settings = get_settings()


class LLMSchemaFactory:
    """
    Orchestrates LLM-driven schema generation.
    Called by the gap optimizer when HIGH/CRITICAL gaps are detected.

    Workflow:
    1. Receive gap analysis result (missing_topics, document content)
    2. Call LLM to generate structured schema suggestions
    3. Parse and validate LLM output
    4. Persist generated schemas to generated_schemas table
    """

    def __init__(self) -> None:
        self._jsonld = JSONLDGenerator()
        self._metadata = MetadataGenerator()
        self._ontology = OntologyBuilder()

    async def generate_from_gap(
        self,
        document_id: str,
        tenant_id: str,
        missing_topics: list[str],
        content_excerpt: str,
        schema_type: str = "Article",
    ) -> dict[str, Any]:
        """
        Generate schemas to address identified content gaps.

        Returns:
            Dict with generated schemas (jsonld, metadata, ontology).
        """
        # Try LLM-enhanced generation first, fall back to rule-based
        try:
            llm_schema = await self._llm_generate(
                missing_topics=missing_topics,
                content_excerpt=content_excerpt,
                schema_type=schema_type,
            )
        except Exception as exc:
            logger.warning("llm_schema_generation_failed_using_fallback", error=str(exc))
            llm_schema = None

        # Rule-based JSON-LD generation
        jsonld_schema = self._jsonld.generate(
            schema_type=schema_type,
            content=content_excerpt,
            metadata={
                "keywords": missing_topics[:10],
                "description": f"Content covering: {', '.join(missing_topics[:3])}",
            },
        )

        # Metadata bundle
        metadata_bundle = self._metadata.generate(
            content=content_excerpt,
            metadata={"keywords": missing_topics},
        )

        # Ontology from missing topics
        ontology = self._ontology.build_from_topics(missing_topics)

        result = {
            "document_id":  document_id,
            "tenant_id":    tenant_id,
            "jsonld":       llm_schema or jsonld_schema,
            "metadata":     {
                "title":        metadata_bundle.title,
                "description":  metadata_bundle.description,
                "keywords":     metadata_bundle.keywords,
                "open_graph":   metadata_bundle.open_graph,
                "twitter_card": metadata_bundle.twitter_card,
            },
            "ontology":     ontology.to_dict(),
            "missing_topics_addressed": missing_topics[:10],
        }

        await self._persist(result)
        logger.info("llm_schema_factory_complete", document_id=document_id)
        return result

    async def _llm_generate(
        self,
        missing_topics: list[str],
        content_excerpt: str,
        schema_type: str,
    ) -> dict | None:
        """Call OpenAI to generate an enhanced JSON-LD schema."""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        if not settings.openai_api_key:
            return None

        prompt = f"""Generate a valid Schema.org JSON-LD schema of type '{schema_type}' 
for content that covers these topics: {', '.join(missing_topics[:5])}.
Base it on this content excerpt: {content_excerpt[:500]}
Return only valid JSON-LD, no explanation."""

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=800,
        )

        raw = response.choices[0].message.content or ""
        # Strip markdown code fences if present
        import re
        raw = re.sub(r"```(?:json|jsonld)?\s*|\s*```", "", raw).strip()
        return json.loads(raw)

    async def _persist(self, result: dict) -> None:
        """Persist generated schema to the database."""
        try:
            from configs.database import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as session:
                await session.execute(
                    text("""
                        INSERT INTO generated_schemas
                            (document_id, tenant_id, schema_type, format, content, metadata)
                        VALUES
                            (:document_id, :tenant_id, :schema_type, 'jsonld',
                             :content::jsonb, :metadata::jsonb)
                    """),
                    {
                        "document_id": result["document_id"],
                        "tenant_id":   result["tenant_id"],
                        "schema_type": result.get("jsonld", {}).get("@type", "Article"),
                        "content":     json.dumps(result["jsonld"]),
                        "metadata":    json.dumps(result["metadata"]),
                    },
                )
                await session.commit()
        except Exception as exc:
            logger.error("schema_persist_failed", error=str(exc))
