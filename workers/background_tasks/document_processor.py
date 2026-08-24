"""
DATA ENGINE — Background Document Processor
Lightweight async processor for non-Celery document operations.
Used for small, in-process background tasks that don't need
a full Celery worker (e.g. post-upload metadata extraction).
"""

import asyncio
import structlog
from typing import Any

logger = structlog.get_logger(__name__)


class BackgroundDocumentProcessor:
    """
    Processes document metadata extraction and tagging
    asynchronously without Celery overhead.

    Suitable for quick enrichment tasks:
    - Language detection
    - Word count / reading time
    - Preview snippet extraction
    """

    async def enrich_document_metadata(
        self,
        document_id: str,
        raw_text: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        """
        Compute and persist lightweight metadata about a document.

        Returns a dict of computed metadata fields.
        """
        from shared.utils.text import count_words, count_sentences, detect_language_simple
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text
        import json

        word_count = count_words(raw_text)
        sentence_count = count_sentences(raw_text)
        language = detect_language_simple(raw_text)
        reading_time_minutes = round(word_count / 200, 1)  # ~200 wpm average
        preview = raw_text[:300].strip()

        metadata = {
            "word_count":            word_count,
            "sentence_count":        sentence_count,
            "language":              language,
            "reading_time_minutes":  reading_time_minutes,
            "preview":               preview,
        }

        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text("""
                        UPDATE documents
                        SET
                            language = :language,
                            metadata = metadata || :metadata::jsonb
                        WHERE id = :id AND tenant_id = :tenant_id
                    """),
                    {
                        "language":  language,
                        "metadata":  json.dumps(metadata),
                        "id":        document_id,
                        "tenant_id": tenant_id,
                    },
                )
                await session.commit()
        except Exception as exc:
            logger.error("metadata_enrichment_failed", document_id=document_id, error=str(exc))

        logger.debug("document_metadata_enriched", document_id=document_id, **metadata)
        return metadata
