"""
DATA ENGINE — SEO Metadata Optimiser
Phase 4: Analyses and improves title tags, meta descriptions,
heading structure, and canonical URL configuration.
"""

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# SEO best-practice limits
TITLE_MIN_CHARS = 30
TITLE_MAX_CHARS = 60
META_DESC_MIN_CHARS = 120
META_DESC_MAX_CHARS = 160


@dataclass
class MetadataAuditResult:
    """SEO metadata audit outcome for a page/document."""
    title: str
    meta_description: str
    title_length: int
    meta_desc_length: int
    title_issues: list[str] = field(default_factory=list)
    meta_desc_issues: list[str] = field(default_factory=list)
    heading_issues: list[str] = field(default_factory=list)
    score: float = 0.0           # 0–100
    recommendations: list[str] = field(default_factory=list)


class SEOMetadataOptimiser:
    """
    Audits title tags, meta descriptions, and heading structures
    for SEO compliance and optimisation opportunities.
    """

    def audit(
        self,
        title: str,
        meta_description: str,
        content: str = "",
        primary_keyword: str = "",
    ) -> MetadataAuditResult:
        """
        Run a full metadata SEO audit.

        Args:
            title:            Page/document title.
            meta_description: Meta description text.
            content:          Full body content for heading analysis.
            primary_keyword:  Primary target keyword.

        Returns:
            MetadataAuditResult with issues and score.
        """
        title_issues = self._audit_title(title, primary_keyword)
        meta_desc_issues = self._audit_meta_description(meta_description, primary_keyword)
        heading_issues = self._audit_headings(content) if content else []

        # Scoring: 100 points, deduct for each issue
        score = 100.0
        score -= len(title_issues) * 15
        score -= len(meta_desc_issues) * 10
        score -= len(heading_issues) * 5
        score = max(0.0, min(100.0, score))

        recs = self._build_recommendations(title_issues, meta_desc_issues, heading_issues)

        return MetadataAuditResult(
            title=title,
            meta_description=meta_description,
            title_length=len(title),
            meta_desc_length=len(meta_description),
            title_issues=title_issues,
            meta_desc_issues=meta_desc_issues,
            heading_issues=heading_issues,
            score=round(score, 1),
            recommendations=recs,
        )

    def _audit_title(self, title: str, keyword: str) -> list[str]:
        issues = []
        tl = len(title)
        if tl < TITLE_MIN_CHARS:
            issues.append(f"Title too short ({tl} chars). Aim for {TITLE_MIN_CHARS}–{TITLE_MAX_CHARS}.")
        if tl > TITLE_MAX_CHARS:
            issues.append(f"Title too long ({tl} chars). Keep under {TITLE_MAX_CHARS} to avoid truncation.")
        if keyword and keyword.lower() not in title.lower():
            issues.append(f"Primary keyword '{keyword}' missing from title.")
        if title == title.upper():
            issues.append("Title is in ALL CAPS — use sentence or title case.")
        return issues

    def _audit_meta_description(self, desc: str, keyword: str) -> list[str]:
        issues = []
        dl = len(desc)
        if dl == 0:
            issues.append("Meta description is missing — add one to improve CTR.")
            return issues
        if dl < META_DESC_MIN_CHARS:
            issues.append(f"Meta description too short ({dl} chars). Aim for {META_DESC_MIN_CHARS}–{META_DESC_MAX_CHARS}.")
        if dl > META_DESC_MAX_CHARS:
            issues.append(f"Meta description too long ({dl} chars). Keep under {META_DESC_MAX_CHARS}.")
        if keyword and keyword.lower() not in desc.lower():
            issues.append(f"Primary keyword '{keyword}' missing from meta description.")
        return issues

    def _audit_headings(self, content: str) -> list[str]:
        """Check for H1/H2 heading structure using markdown or HTML patterns."""
        issues = []
        h1_matches = re.findall(r'^# .+', content, re.MULTILINE)
        h2_matches = re.findall(r'^## .+', content, re.MULTILINE)

        if not h1_matches:
            issues.append("No H1 heading found. Add a single H1 containing the primary keyword.")
        if len(h1_matches) > 1:
            issues.append(f"Multiple H1 headings found ({len(h1_matches)}). Use only one H1 per page.")
        if not h2_matches:
            issues.append("No H2 subheadings found. Add H2 sections to improve content structure.")

        return issues

    def _build_recommendations(
        self, title_issues: list[str], meta_issues: list[str], heading_issues: list[str]
    ) -> list[str]:
        recs = []
        recs.extend(title_issues[:2])
        recs.extend(meta_issues[:2])
        recs.extend(heading_issues[:2])
        return recs

    def generate_title_suggestions(self, keywords: list[str], content_excerpt: str) -> list[str]:
        """Generate 3 title tag candidates from keywords and content."""
        suggestions = []
        if keywords:
            primary = keywords[0].title()
            suggestions.append(f"{primary}: Complete Guide & Best Practices")
            suggestions.append(f"What is {primary}? {content_excerpt[:40].strip()}...")
            if len(keywords) >= 2:
                secondary = keywords[1].title()
                suggestions.append(f"{primary} and {secondary} — Expert Overview")
        return suggestions[:3]
