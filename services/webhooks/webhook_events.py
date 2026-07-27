"""
DATA ENGINE — Webhook Event Payload Builders

One builder function per event type.  Each returns a clean, versioned
dict that becomes the `data` field of the signed webhook payload.

Event catalogue
───────────────
  document.completed      — document fully ingested, chunked, and embedded
  document.failed         — processing pipeline failed
  gap.detected            — gap analysis complete; includes severity + topics
  drafts.ready            — LLM writing agent finished all content drafts
  gap.resolved            — gap score dropped below LOW threshold (gap closed)
  schema.generated        — JSON-LD / structured data schemas created
  ranking.updated         — daily SERP rank snapshot refreshed for a keyword

Products subscribe to whichever subset they care about, or use "*" to
receive everything.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any


def _base(event_version: str = "1.0") -> dict[str, Any]:
    return {
        "engine_version": event_version,
        "generated_at":   datetime.now(UTC).isoformat(),
    }


# ── Event builders ────────────────────────────────────────────────────────────

def build_document_completed(
    document_id: str,
    tenant_id: str,
    filename: str,
    chunk_count: int,
    status: str = "completed",
) -> dict[str, Any]:
    return {
        **_base(),
        "document_id": document_id,
        "tenant_id":   tenant_id,
        "filename":    filename,
        "chunk_count": chunk_count,
        "status":      status,
    }


def build_document_failed(
    document_id: str,
    tenant_id: str,
    filename: str,
    error_message: str,
) -> dict[str, Any]:
    return {
        **_base(),
        "document_id":   document_id,
        "tenant_id":     tenant_id,
        "filename":      filename,
        "error_message": error_message,
    }


def build_gap_detected(
    document_id: str,
    tenant_id: str,
    gap_score: float,
    severity: str,
    missing_topics: list[str],
    before_coverage: float,
    top_priority_clusters: list[str] | None = None,
    estimated_words_needed: int = 0,
) -> dict[str, Any]:
    return {
        **_base(),
        "document_id":            document_id,
        "tenant_id":              tenant_id,
        "gap_score":              gap_score,
        "severity":               severity,
        "missing_topics":         missing_topics[:10],
        "missing_topics_count":   len(missing_topics),
        "before_coverage":        before_coverage,
        "top_priority_clusters":  (top_priority_clusters or [])[:5],
        "estimated_words_needed": estimated_words_needed,
        "action_required":        severity in ("high", "critical"),
    }


def build_drafts_ready(
    document_id: str,
    tenant_id: str,
    drafts_written: int,
    total_words: int,
    topics_covered: list[str],
    model_used: str,
    provider_used: str,
    draft_fetch_url: str = "",
) -> dict[str, Any]:
    return {
        **_base(),
        "document_id":    document_id,
        "tenant_id":      tenant_id,
        "drafts_written": drafts_written,
        "total_words":    total_words,
        "topics_covered": topics_covered,
        "model_used":     model_used,
        "provider_used":  provider_used,
        # Convenience: products can GET this URL to retrieve all draft text
        "draft_fetch_url": draft_fetch_url,
        "message": (
            f"{drafts_written} content section(s) generated ({total_words:,} words). "
            "Fetch drafts at the URL above to publish them to your product."
        ),
    }


def build_gap_resolved(
    document_id: str,
    tenant_id: str,
    gap_score: float,
    after_coverage: float,
    before_coverage: float,
) -> dict[str, Any]:
    improvement = round(after_coverage - before_coverage, 4)
    return {
        **_base(),
        "document_id":   document_id,
        "tenant_id":     tenant_id,
        "gap_score":     gap_score,
        "after_coverage":  after_coverage,
        "before_coverage": before_coverage,
        "coverage_improvement": improvement,
        "message": (
            f"Gap resolved. Coverage improved by "
            f"{improvement * 100:.1f}% (from "
            f"{before_coverage * 100:.1f}% → {after_coverage * 100:.1f}%)."
        ),
    }


def build_schema_generated(
    document_id: str,
    tenant_id: str,
    schema_type: str,
    missing_topics_addressed: list[str],
    citation_score: float,
    zero_click_score: float,
    script_tag_preview: str = "",
) -> dict[str, Any]:
    return {
        **_base(),
        "document_id":              document_id,
        "tenant_id":                tenant_id,
        "schema_type":              schema_type,
        "missing_topics_addressed": missing_topics_addressed[:10],
        "citation_score":           citation_score,
        "zero_click_score":         zero_click_score,
        "script_tag_preview":       script_tag_preview[:500] if script_tag_preview else "",
        "message": (
            f"{schema_type} JSON-LD schema generated. "
            f"Citation readiness: {citation_score * 100:.0f}%. "
            "Add the script_tag to your page <head>."
        ),
    }


def build_ranking_updated(
    domain: str,
    tenant_id: str,
    keyword: str,
    position: int | None,
    previous_position: int | None,
    search_volume: int | None,
    snapshot_date: str,
    location_code: int = 2840,
) -> dict[str, Any]:
    moved = None
    if position is not None and previous_position is not None:
        moved = previous_position - position   # positive = moved up

    return {
        **_base(),
        "tenant_id":         tenant_id,
        "domain":            domain,
        "keyword":           keyword,
        "position":          position,
        "previous_position": previous_position,
        "positions_moved":   moved,
        "ranking_direction": (
            "up" if (moved and moved > 0)
            else "down" if (moved and moved < 0)
            else "unchanged"
        ),
        "search_volume":     search_volume,
        "location_code":     location_code,
        "snapshot_date":     snapshot_date,
        "ranked_first":      position == 1,
    }
