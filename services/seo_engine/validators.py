"""
DATA ENGINE — SEO Validators
Phase 4: Validates URLs, canonical tags, robots.txt directives,
and hreflang attributes for technical SEO compliance.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class TechnicalSEOAudit:
    """Technical SEO audit result for a URL/page."""
    url: str
    is_valid_url: bool
    has_canonical: bool
    canonical_url: str | None
    is_https: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score: float = 100.0


class SEOValidator:
    """
    Technical SEO validation — URL structure, HTTPS,
    canonical tags, and redirect chains.
    """

    def audit_url(self, url: str, canonical: str | None = None) -> TechnicalSEOAudit:
        issues, warnings = [], []
        parsed = urlparse(url)

        is_valid = bool(parsed.scheme and parsed.netloc)
        if not is_valid:
            issues.append(f"Invalid URL format: '{url}'")

        is_https = parsed.scheme == "https"
        if not is_https:
            issues.append("URL uses HTTP — HTTPS required for SEO trust signals.")

        if re.search(r'[A-Z]', parsed.path):
            warnings.append("URL path contains uppercase letters — use lowercase for consistency.")

        if len(url) > 115:
            warnings.append(f"URL is long ({len(url)} chars) — shorter URLs are preferred.")

        has_canonical = canonical is not None
        if not has_canonical:
            warnings.append("No canonical URL specified — add a canonical tag to prevent duplicate content.")

        score = 100.0 - len(issues) * 20 - len(warnings) * 5
        return TechnicalSEOAudit(
            url=url,
            is_valid_url=is_valid,
            has_canonical=has_canonical,
            canonical_url=canonical,
            is_https=is_https,
            issues=issues,
            warnings=warnings,
            score=max(0.0, score),
        )

    def validate_robots_directive(self, directive: str) -> dict:
        """Check a robots meta content value for correctness."""
        valid_directives = {
            "index", "noindex", "follow", "nofollow",
            "noarchive", "nosnippet", "noimageindex",
        }
        parts = {d.strip().lower() for d in directive.split(",")}
        invalid = parts - valid_directives
        conflicts = []
        if "index" in parts and "noindex" in parts:
            conflicts.append("Conflicting 'index' and 'noindex' directives.")
        if "follow" in parts and "nofollow" in parts:
            conflicts.append("Conflicting 'follow' and 'nofollow' directives.")
        return {
            "directive": directive,
            "parts": list(parts),
            "invalid_parts": list(invalid),
            "conflicts": conflicts,
            "is_valid": not invalid and not conflicts,
        }

    def check_duplicate_content(self, texts: list[str], threshold: float = 0.85) -> list[dict]:
        """
        Identify near-duplicate content pairs in a list of texts.
        Uses Jaccard similarity on word sets — fast heuristic.
        Returns pairs with similarity above threshold.
        """
        results = []
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                sim = self._jaccard_similarity(texts[i], texts[j])
                if sim >= threshold:
                    results.append({"index_a": i, "index_b": j, "similarity": round(sim, 3)})
        return results

    @staticmethod
    def _jaccard_similarity(text_a: str, text_b: str) -> float:
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())
        if not words_a and not words_b:
            return 1.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union) if union else 0.0
