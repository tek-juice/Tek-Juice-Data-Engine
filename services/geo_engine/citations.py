"""
DATA ENGINE — GEO Citation Readiness
Phase 4: Analyses content for citation-readiness in AI-generated responses.
AI models cite content that is specific, attributable, and verifiable.
"""

import re
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class CitationReadinessResult:
    """Assessment of how citable a piece of content is for AI systems."""
    overall_score: float            # 0–100
    has_statistics: bool
    has_dates: bool
    has_named_sources: bool
    has_direct_quotes: bool
    has_structured_lists: bool
    statistics_count: int
    source_references: list[str]
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


class CitationReadinessAnalyser:
    """
    Analyses content for properties that make it citable
    by generative AI systems.

    AI citation factors:
    - Specific statistics and numbers
    - Named sources and attributions
    - Clear dates and timeframes
    - Direct quotes and verbatim passages
    - Structured lists and tables
    - Factual density per paragraph
    """

    def analyse(self, content: str) -> CitationReadinessResult:
        """
        Score content for AI citation readiness.

        Args:
            content: Plain text content to analyse.

        Returns:
            CitationReadinessResult with score and recommendations.
        """
        statistics = self._find_statistics(content)
        dates = self._find_dates(content)
        sources = self._find_sources(content)
        quotes = self._find_direct_quotes(content)
        lists = self._find_structured_lists(content)

        has_stats = len(statistics) > 0
        has_dates = len(dates) > 0
        has_sources = len(sources) > 0
        has_quotes = len(quotes) > 0
        has_lists = lists > 0

        # Scoring weights
        score = 0.0
        score += 25.0 if has_stats else 0.0
        score += 20.0 if has_sources else 0.0
        score += 20.0 if has_dates else 0.0
        score += 20.0 if has_quotes else 0.0
        score += 15.0 if has_lists else 0.0

        # Bonus for multiple statistics
        if len(statistics) >= 5:
            score = min(100.0, score + 10.0)

        issues = self._identify_issues(has_stats, has_sources, has_dates, content)
        recs = self._generate_recommendations(has_stats, has_sources, has_dates, has_quotes, has_lists)

        return CitationReadinessResult(
            overall_score=round(score, 1),
            has_statistics=has_stats,
            has_dates=has_dates,
            has_named_sources=has_sources,
            has_direct_quotes=has_quotes,
            has_structured_lists=has_lists > 0,
            statistics_count=len(statistics),
            source_references=sources[:10],
            issues=issues,
            recommendations=recs,
        )

    @staticmethod
    def _find_statistics(content: str) -> list[str]:
        """Find numerical statistics, percentages, and metrics."""
        pattern = r'\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|percent|million|billion|trillion|x|times|KB|MB|GB|TB|ms|seconds?|minutes?|hours?)?\b'
        return re.findall(pattern, content, re.IGNORECASE)

    @staticmethod
    def _find_dates(content: str) -> list[str]:
        """Find date references in various formats."""
        patterns = [
            r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b',
            r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b',
            r'\b(?:Q[1-4]\s+)?\d{4}\b',
            r'\b(?:in|since|by|after|before)\s+\d{4}\b',
        ]
        found = []
        for p in patterns:
            found.extend(re.findall(p, content, re.IGNORECASE))
        return list(set(found))

    @staticmethod
    def _find_sources(content: str) -> list[str]:
        """Find named source references (according to, per, said, reported)."""
        pattern = r'(?:according to|per|said|reported by|study by|research by|published by|cited by)\s+([A-Z][^,.\n]{3,50})'
        matches = re.findall(pattern, content, re.IGNORECASE)
        return [m.strip() for m in matches]

    @staticmethod
    def _find_direct_quotes(content: str) -> list[str]:
        """Find quoted text passages."""
        return re.findall(r'"([^"]{10,200})"', content)

    @staticmethod
    def _find_structured_lists(content: str) -> int:
        """Count list items (markdown bullets, numbered lists)."""
        bullets = re.findall(r'^\s*[-*•]\s+.+', content, re.MULTILINE)
        numbered = re.findall(r'^\s*\d+[.)]\s+.+', content, re.MULTILINE)
        return len(bullets) + len(numbered)

    @staticmethod
    def _identify_issues(
        has_stats: bool, has_sources: bool, has_dates: bool, content: str
    ) -> list[str]:
        issues = []
        if not has_stats:
            issues.append("No statistics found — AI models prefer quantified claims.")
        if not has_sources:
            issues.append("No source attributions found — add 'according to [source]' references.")
        if not has_dates:
            issues.append("No dates found — temporal context improves citation confidence.")
        if len(content.split()) < 200:
            issues.append("Content is too short for reliable AI citation (<200 words).")
        return issues

    @staticmethod
    def _generate_recommendations(
        has_stats: bool, has_sources: bool, has_dates: bool,
        has_quotes: bool, has_lists: bool
    ) -> list[str]:
        recs = []
        if not has_stats:
            recs.append("Add specific statistics: percentages, counts, or performance metrics.")
        if not has_sources:
            recs.append("Attribute claims to named sources, studies, or publications.")
        if not has_dates:
            recs.append("Include publication date and relevant timeframes.")
        if not has_quotes:
            recs.append("Add direct quotes from authoritative sources.")
        if not has_lists:
            recs.append("Use bullet lists or numbered steps — AI models excerpt lists readily.")
        return recs
