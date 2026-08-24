"""
DATA ENGINE — Gap Recommendations
Generates structured, actionable content recommendations
from gap analysis results.
"""

from dataclasses import dataclass
from configs.constants import GapSeverity


@dataclass
class Recommendation:
    """A single actionable content recommendation."""
    priority: int            # 1 = highest priority
    action_type: str         # expand | create | restructure | update_metadata
    topic: str
    description: str
    estimated_impact: str    # low | medium | high


class RecommendationEngine:
    """
    Generates ranked content recommendations from gap analysis results.
    Used by the dashboard before/after comparison view.
    """

    def generate(
        self,
        missing_topics: list[str],
        covered_topics: list[str],
        gap_score: float,
        severity: str,
    ) -> list[Recommendation]:
        """
        Generate a prioritised list of recommendations.

        Args:
            missing_topics: Topics not covered by the document.
            covered_topics: Topics already covered.
            gap_score:      Overall gap score (0.0–1.0).
            severity:       Gap severity classification.

        Returns:
            List of Recommendation objects sorted by priority.
        """
        recs: list[Recommendation] = []
        priority = 1

        # Critical/high gap — create new content first
        if severity in (GapSeverity.CRITICAL.value, GapSeverity.HIGH.value):
            recs.append(Recommendation(
                priority=priority,
                action_type="restructure",
                topic="Content Architecture",
                description=(
                    f"Document has a {round(gap_score * 100, 1)}% content gap. "
                    "Consider restructuring to address the missing topics below."
                ),
                estimated_impact="high",
            ))
            priority += 1

        # Per-topic create recommendations
        for topic in missing_topics[:10]:
            recs.append(Recommendation(
                priority=priority,
                action_type="create",
                topic=topic,
                description=f"Create new content section covering: {topic}",
                estimated_impact="high" if severity in (GapSeverity.CRITICAL.value, GapSeverity.HIGH.value) else "medium",
            ))
            priority += 1

        # Expand existing covered topics with better depth
        for topic in covered_topics[:5]:
            recs.append(Recommendation(
                priority=priority,
                action_type="expand",
                topic=topic,
                description=f"Expand existing content on '{topic}' with more depth and recent examples.",
                estimated_impact="medium",
            ))
            priority += 1

        # Always recommend metadata update
        recs.append(Recommendation(
            priority=priority,
            action_type="update_metadata",
            topic="Schema & Metadata",
            description="Update JSON-LD schema and meta descriptions to reflect latest content.",
            estimated_impact="medium",
        ))

        return sorted(recs, key=lambda r: r.priority)

    def to_dict_list(self, recommendations: list[Recommendation]) -> list[dict]:
        return [
            {
                "priority":         r.priority,
                "action_type":      r.action_type,
                "topic":            r.topic,
                "description":      r.description,
                "estimated_impact": r.estimated_impact,
            }
            for r in recommendations
        ]
