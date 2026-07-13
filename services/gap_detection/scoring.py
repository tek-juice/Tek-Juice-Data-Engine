"""
DATA ENGINE — Gap Scoring
Computes normalised gap scores and severity classifications.
"""

from configs.constants import GapSeverity, GAP_SCORE_THRESHOLDS


def compute_gap_score(
    uncovered_count: int,
    total_trends: int,
    avg_max_similarity: float,
) -> float:
    """
    Compute a composite gap score combining coverage ratio and similarity depth.

    Formula:
        gap_score = 0.7 * (1 - coverage_ratio) + 0.3 * (1 - avg_max_similarity)

    Args:
        uncovered_count:    Number of trends not covered by the document.
        total_trends:       Total number of trend signals analysed.
        avg_max_similarity: Average of per-trend maximum similarity scores.

    Returns:
        Normalised gap score in [0.0, 1.0]. Higher = larger gap.
    """
    if total_trends == 0:
        return 0.0

    coverage_ratio = 1.0 - (uncovered_count / total_trends)
    depth_score = avg_max_similarity

    gap = 0.7 * (1.0 - coverage_ratio) + 0.3 * (1.0 - depth_score)
    return round(min(1.0, max(0.0, gap)), 4)


def classify_severity(gap_score: float) -> GapSeverity:
    """Map a gap score to a severity enum value."""
    if gap_score >= GAP_SCORE_THRESHOLDS[GapSeverity.CRITICAL]:
        return GapSeverity.CRITICAL
    elif gap_score >= GAP_SCORE_THRESHOLDS[GapSeverity.HIGH]:
        return GapSeverity.HIGH
    elif gap_score >= GAP_SCORE_THRESHOLDS[GapSeverity.MEDIUM]:
        return GapSeverity.MEDIUM
    return GapSeverity.LOW


def normalise_score(raw: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """Clamp and normalise a score to [0.0, 1.0]."""
    if max_val == min_val:
        return 0.0
    return round((raw - min_val) / (max_val - min_val), 4)


def priority_rank(gap_score: float, document_age_days: int, view_count: int = 0) -> float:
    """
    Compute a priority rank for scheduling gap remediation.
    Higher rank = fix this document first.

    Factors:
      - Gap score (primary)
      - Document age (older = lower priority)
      - View count (more views = higher priority)
    """
    age_factor = max(0.0, 1.0 - (document_age_days / 365))
    view_factor = min(1.0, view_count / 1000) if view_count else 0.0
    return round(0.6 * gap_score + 0.2 * age_factor + 0.2 * view_factor, 4)
