"""
DATA ENGINE — Gap Detection Analyzer
Phase 2: Identifies missing information and optimisation opportunities
by comparing document embeddings against scraped trend vectors.
"""

import json
import structlog
from dataclasses import dataclass, field
from datetime import datetime, UTC

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from configs.constants import GapSeverity, GAP_SCORE_THRESHOLDS
from configs.settings import get_settings
from services.semantic_engine.comparator import SemanticComparator

logger = structlog.get_logger(__name__)
settings = get_settings()


@dataclass
class GapAnalysisResult:
    """Full result of a gap analysis run on a document."""
    document_id: str
    tenant_id: str
    gap_score: float
    severity: str
    missing_topics: list[str]
    covered_topics: list[str]
    recommendations: list[str]
    before_coverage: float
    after_coverage: float | None = None
    reference_trend_ids: list[str] = field(default_factory=list)
    analysed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class GapAnalyzer:
    """
    Core gap detection engine.

    Process:
    1. Load document embeddings from the vault.
    2. Load recent trend embeddings from scraped_trends.
    3. Compute coverage matrix via SemanticComparator.
    4. Identify uncovered trends as gaps.
    5. Score overall gap severity.
    6. Generate actionable recommendations.
    7. Persist result to gap_analysis_results table.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._comparator = SemanticComparator()

    async def analyse(
        self,
        document_id: str,
        tenant_id: str,
        coverage_threshold: float = 0.70,
        trend_days: int = 7,
    ) -> GapAnalysisResult:
        """
        Run gap analysis for a document against recent trends.

        Args:
            document_id:        Document UUID to analyse.
            tenant_id:          Tenant UUID for RLS scoping.
            coverage_threshold: Cosine similarity required to consider a trend covered.
            trend_days:         How many days of trend data to compare against.

        Returns:
            GapAnalysisResult with full gap breakdown and recommendations.
        """
        from configs.security import set_tenant_context_sql
        await self._session.execute(text(set_tenant_context_sql(tenant_id)))

        doc_vectors, doc_texts = await self._load_document_vectors(document_id, tenant_id)
        trend_vectors, trend_data = await self._load_trend_vectors(days=trend_days)

        if not doc_vectors:
            logger.warning("gap_analysis_no_doc_vectors", document_id=document_id)
            return self._empty_result(document_id, tenant_id)

        if not trend_vectors:
            logger.warning("gap_analysis_no_trend_vectors")
            empty = self._empty_result(document_id, tenant_id)
            try:
                await self._persist(empty)
                await self._session.commit()
            except Exception:
                pass
            return empty

        # Compute coverage
        before_coverage = self._comparator.average_coverage_score(
            doc_vectors, trend_vectors, threshold=coverage_threshold
        )

        # Find uncovered trends
        import numpy as np
        matrix = self._comparator.coverage_matrix(doc_vectors, trend_vectors)
        np_matrix = np.array(matrix)
        max_per_trend = np_matrix.max(axis=0)

        covered_indices = [i for i, s in enumerate(max_per_trend) if s >= coverage_threshold]
        gap_indices = [i for i, s in enumerate(max_per_trend) if s < coverage_threshold]

        missing_topics = [trend_data[i]["title"] or trend_data[i]["query"] for i in gap_indices if i < len(trend_data)]
        covered_topics = [trend_data[i]["title"] or trend_data[i]["query"] for i in covered_indices if i < len(trend_data)]
        reference_ids = [str(trend_data[i]["id"]) for i in gap_indices if i < len(trend_data)]

        gap_score = 1.0 - before_coverage
        severity = self._classify_severity(gap_score)
        recommendations = self._generate_recommendations(missing_topics, gap_score)

        result = GapAnalysisResult(
            document_id=document_id,
            tenant_id=tenant_id,
            gap_score=round(gap_score, 4),
            severity=severity,
            missing_topics=missing_topics[:20],
            covered_topics=covered_topics[:20],
            recommendations=recommendations,
            before_coverage=round(before_coverage, 4),
            reference_trend_ids=reference_ids[:50],
        )

        try:
            await self._persist(result)
            await self._session.commit()
        except Exception as _persist_exc:
            logger.warning("gap_persist_failed", error=str(_persist_exc))

        logger.info(
            "gap_analysis_complete",
            document_id=document_id,
            gap_score=result.gap_score,
            severity=severity,
            missing=len(missing_topics),
        )
        return result

    async def _load_document_vectors(
        self, document_id: str, tenant_id: str
    ) -> tuple[list[list[float]], list[str]]:
        from configs.settings import get_settings as _get_settings
        _dims = _get_settings().embedding_dimension
        _col = f"embedding_{_dims}"
        result = await self._session.execute(
            text(f"""
                SELECT e.{_col} AS embedding, c.text
                FROM embeddings e
                JOIN chunks c ON c.id = e.chunk_id
                WHERE e.document_id = :doc_id AND e.tenant_id = :tenant_id
                  AND e.{_col} IS NOT NULL
                LIMIT 500
            """),
            {"doc_id": document_id, "tenant_id": tenant_id},
        )
        rows = result.fetchall()
        vectors = [list(row.embedding) for row in rows]
        texts = [row.text for row in rows]
        return vectors, texts

    async def _load_trend_vectors(self, days: int = 7) -> tuple[list[list[float]], list[dict]]:
        from configs.settings import get_settings as _get_settings
        _dims = _get_settings().embedding_dimension
        _col = f"embedding_{_dims}"

        # Check the column exists before querying — older deployments may be missing it
        col_check = await self._session.execute(
            text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'scraped_trends' AND column_name = :col
                LIMIT 1
            """),
            {"col": _col},
        )
        if not col_check.fetchone():
            logger.warning("trend_embedding_col_missing", column=_col)
            return [], []

        result = await self._session.execute(
            text(f"""
                SELECT id, title, query, {_col} AS embedding
                FROM scraped_trends
                WHERE {_col} IS NOT NULL
                  AND scraped_at >= NOW() - INTERVAL '{days} days'
                ORDER BY scraped_at DESC
                LIMIT 200
            """)
        )
        rows = result.fetchall()
        vectors = [list(row.embedding) for row in rows]
        data = [
            {
                "id":    str(row.id),
                "title": row.title or "",
                "query": row.query or "",
            }
            for row in rows
        ]
        return vectors, data

    async def _persist(self, result: GapAnalysisResult) -> None:
        # missing_topics and recommendations are TEXT[] — pass Python lists directly;
        # asyncpg maps list[str] → text[] automatically.
        missing = result.missing_topics or []
        recs    = result.recommendations or []
        await self._session.execute(
            text("""
                INSERT INTO gap_analysis_results
                    (tenant_id, document_id, gap_score, severity,
                     before_coverage, after_coverage,
                     missing_topics, recommendations)
                VALUES
                    (:tenant_id, :document_id, :gap_score, :severity,
                     :before_coverage, :after_coverage,
                     :missing_topics, :recommendations)
            """),
            {
                "tenant_id":       str(result.tenant_id),
                "document_id":     str(result.document_id),
                "gap_score":       float(result.gap_score),
                "severity":        str(result.severity),
                "before_coverage": result.before_coverage,
                "after_coverage":  result.after_coverage,
                "missing_topics":  missing,
                "recommendations": recs,
            },
        )

    @staticmethod
    def _classify_severity(gap_score: float) -> str:
        for severity, threshold in reversed(list(GAP_SCORE_THRESHOLDS.items())):
            if gap_score >= threshold:
                return severity.value
        return GapSeverity.LOW.value

    @staticmethod
    def _generate_recommendations(missing_topics: list[str], gap_score: float) -> list[str]:
        recs = []
        if gap_score >= 0.75:
            recs.append("High content gap detected. Significant new content creation recommended.")
        elif gap_score >= 0.50:
            recs.append("Moderate content gap. Consider expanding existing sections.")
        else:
            recs.append("Minor content gap. Small updates may improve coverage.")

        for topic in missing_topics[:5]:
            recs.append(f"Add content covering: {topic}")
        return recs

    @staticmethod
    def _empty_result(document_id: str, tenant_id: str) -> GapAnalysisResult:
        return GapAnalysisResult(
            document_id=document_id,
            tenant_id=tenant_id,
            gap_score=0.0,
            severity=GapSeverity.LOW.value,
            missing_topics=[],
            covered_topics=[],
            recommendations=["Insufficient data for gap analysis."],
            before_coverage=0.0,
        )
