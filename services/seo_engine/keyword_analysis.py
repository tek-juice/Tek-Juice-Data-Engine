"""
DATA ENGINE — SEO Keyword Analysis
Phase 4: Analyses keyword coverage, density, and search intent
alignment within document content.
"""

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class KeywordAnalysisResult:
    """Results of keyword analysis on a piece of content."""
    target_keywords: list[str]
    matched_keywords: list[str]
    missing_keywords: list[str]
    keyword_density: dict[str, float]
    total_words: int
    coverage_score: float          # 0.0–1.0
    density_issues: list[str]      # keywords that are over/under-optimised
    recommendations: list[str]


class KeywordAnalyser:
    """
    Analyses keyword presence, density, and placement within content.

    Optimal keyword density: 0.5%–2.5% per keyword.
    Below 0.5% = under-optimised, above 2.5% = keyword stuffing risk.
    """

    OPTIMAL_DENSITY_MIN = 0.005
    OPTIMAL_DENSITY_MAX = 0.025

    def analyse(
        self,
        content: str,
        target_keywords: list[str],
        title: str = "",
        meta_description: str = "",
    ) -> KeywordAnalysisResult:
        """
        Analyse keyword coverage and density.

        Args:
            content:          Full document text.
            target_keywords:  Keywords to check for.
            title:            Document title for priority placement check.
            meta_description: Meta description for placement check.

        Returns:
            KeywordAnalysisResult with full breakdown.
        """
        content_lower = content.lower()
        word_list = re.findall(r'\b\w+\b', content_lower)
        total_words = len(word_list)

        matched, missing, density, issues = [], [], {}, []

        for keyword in target_keywords:
            kw_lower = keyword.lower()
            # Count occurrences (supports multi-word phrases)
            count = len(re.findall(re.escape(kw_lower), content_lower))

            if count > 0:
                matched.append(keyword)
                dens = count / total_words if total_words > 0 else 0.0
                density[keyword] = round(dens * 100, 3)  # as percentage

                if dens < self.OPTIMAL_DENSITY_MIN:
                    issues.append(f"'{keyword}' is under-used ({dens*100:.2f}% — increase to 0.5%+)")
                elif dens > self.OPTIMAL_DENSITY_MAX:
                    issues.append(f"'{keyword}' may be over-used ({dens*100:.2f}% — reduce below 2.5%)")
            else:
                missing.append(keyword)
                density[keyword] = 0.0

        coverage_score = len(matched) / len(target_keywords) if target_keywords else 0.0

        recs = self._generate_recommendations(
            missing=missing,
            issues=issues,
            title=title,
            target_keywords=target_keywords,
        )

        result = KeywordAnalysisResult(
            target_keywords=target_keywords,
            matched_keywords=matched,
            missing_keywords=missing,
            keyword_density=density,
            total_words=total_words,
            coverage_score=round(coverage_score, 4),
            density_issues=issues,
            recommendations=recs,
        )

        logger.debug(
            "keyword_analysis_complete",
            matched=len(matched),
            missing=len(missing),
            coverage=coverage_score,
        )
        return result

    def _generate_recommendations(
        self,
        missing: list[str],
        issues: list[str],
        title: str,
        target_keywords: list[str],
    ) -> list[str]:
        recs = []
        if missing:
            recs.append(f"Add these missing keywords to your content: {', '.join(missing[:5])}")
        if issues:
            recs.extend(issues[:3])
        if target_keywords and title:
            primary = target_keywords[0].lower()
            if primary not in title.lower():
                recs.append(f"Include primary keyword '{target_keywords[0]}' in the page title.")
        return recs

    def extract_natural_keywords(self, content: str, top_n: int = 20) -> list[tuple[str, int]]:
        """
        Extract the most frequently occurring meaningful words as keyword candidates.

        Returns:
            List of (keyword, frequency) tuples sorted by frequency descending.
        """
        stopwords = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
            "has", "have", "had", "will", "would", "can", "could", "should", "may",
            "this", "that", "these", "those", "it", "its", "they", "their", "we",
            "our", "you", "your", "i", "my", "me", "he", "she", "his", "her",
        }
        words = re.findall(r'\b[a-z]{4,}\b', content.lower())
        freq: dict[str, int] = {}
        for word in words:
            if word not in stopwords:
                freq[word] = freq.get(word, 0) + 1

        return sorted(freq.items(), key=lambda x: x[1], reverse=True)[:top_n]
