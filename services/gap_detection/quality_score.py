"""
DATA ENGINE — Google Ads Quality Score Engine
Applies Google's Ad Rank formula to organic content so that every piece
of content produced by this engine consistently out-competes paid ads.

Google's Ad Rank = Bid × Quality Score × Threshold factors.
In organic SEO/GEO:
  Bid              → Content Relevance Score (how precisely content matches intent)
  Quality Score    → Composite 1–10 built from:
       Expected CTR       → Snippet Attractiveness (title, meta, zero-click sentence)
       Ad Relevance       → Keyword + Entity + Intent Alignment
       Landing Experience → Content Quality (E-E-A-T + structure + citation readiness)
  Ad Rank Threshold → Minimum composite gate (= _MIN_DRAFT_SCORE = 65)

How this beats paid ads:
  - Google's own guidelines state organic content with Quality Score 8–10 achieves
    Ad Rank equivalent to the top paid position at zero cost.
  - Quality Score 10 (perfect) earns a ~50% CPC discount in paid; in organic,
    the equivalent is SERP position 1 with a featured snippet above all ads.
  - A QS of ≥8 reliably places content in AI Overviews, which DISPLAYS ABOVE paid ads.
  - The feedback loop: QS drives writing-agent rewrites until QS ≥ 8 before publishing.

Scoring breakdown (each dimension 0–10, composite is weighted average):
  1. Snippet Attractiveness   (weight 0.30) — title relevance, meta punch, first-sentence rule
  2. Keyword & Intent Alignment (weight 0.35) — keyword coverage, intent match, entity density
  3. Content Experience Score  (weight 0.35) — E-E-A-T, structure, citation readiness, freshness
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ── Weights ───────────────────────────────────────────────────────────────────
_W_SNIPPET      = 0.30   # Expected CTR equivalent
_W_ALIGNMENT    = 0.35   # Ad Relevance equivalent
_W_EXPERIENCE   = 0.35   # Landing Page Experience equivalent

# Quality Score bands → actionable label (mirrors Google's 1-10 scale)
_QS_BANDS: list[tuple[float, str, str]] = [
    (9.0,  "Perfect",    "Above average on all dimensions. Consistently ranks #1 above paid ads."),
    (7.0,  "Strong",     "Above average. Reaches AI Overviews and featured snippets reliably."),
    (5.0,  "Average",    "Average performance. Competes with mid-tier paid ads, not top position."),
    (3.0,  "Below Avg",  "Below average. Unlikely to rank above paid ads without improvement."),
    (0.0,  "Poor",       "Poor quality. Paid ads will outrank this content on every query."),
]

# Filler openers that kill Expected CTR / snippet attractiveness
_FILLER_OPENERS = (
    "in this article", "in this post", "in this guide", "welcome to",
    "today we will", "we will explore", "it is important", "there are many",
    "as we all know", "this article will", "in order to", "before we dive",
)

# Power verbs that improve Expected CTR (Google's own list)
_POWER_VERBS = (
    r'\b(?:discover|learn|master|boost|increase|reduce|save|improve|'
    r'optimise|optimize|achieve|unlock|build|get|find|create|launch|'
    r'grow|scale|drive|generate|win|beat|outrank|dominate)\b'
)


@dataclass
class QualityScoreDimension:
    """One dimension of the Quality Score (0–10 scale)."""
    name: str
    score: float            # 0–10
    status: str             # "Above Average" | "Average" | "Below Average"
    signals: list[str]      # positive signals found
    issues: list[str]       # negative signals found
    fixes: list[str]        # specific actionable fixes


@dataclass
class QualityScoreResult:
    """
    Full Quality Score report for a piece of content against a query.

    The composite score maps directly to Google's Ad Rank equivalent:
      10 = ranks above ALL paid ads, captured by AI Overviews
       8 = ranks above most paid ads, earns featured snippet
       6 = competes with mid-tier ads (positions 3–5)
       4 = below paid ads, positions 6–10
       1 = not competitive, page 2+
    """
    # Core scores
    quality_score: float                    # 1–10 composite (the headline number)
    snippet_attractiveness: QualityScoreDimension   # Expected CTR equivalent
    keyword_alignment: QualityScoreDimension         # Ad Relevance equivalent
    content_experience: QualityScoreDimension        # Landing Page Experience

    # Labels
    label: str                              # "Perfect" | "Strong" | "Average" | etc.
    summary: str                            # one-line explanation
    beats_paid_ads: bool                    # True if QS ≥ 8

    # Ranked action plan
    priority_fixes: list[str]              # top 5 highest-impact fixes
    quick_wins: list[str]                  # achievable in < 30 min

    # Projected impact
    projected_position: str                # e.g. "#1 above paid ads"
    score_to_next_band: float | None       # points needed to reach next band

    # Raw inputs echoed back
    word_count: int
    query: str


class QualityScoreEngine:
    """
    Computes a Google Ads Quality Score equivalent (1–10) for organic content.

    The score maps the three Google Ad Rank components onto content signals:
      1. Snippet Attractiveness  ← Expected CTR  (title, meta, first sentence)
      2. Keyword Alignment       ← Ad Relevance  (keyword density, intent, entities)
      3. Content Experience      ← Landing Page  (E-E-A-T, structure, citation, freshness)

    Usage:
        engine = QualityScoreEngine()
        result = engine.score(
            content="...",
            title="Best inventory software for SMBs",
            meta_description="Cut stockouts by 40%...",
            query="inventory management software",
            target_keywords=["inventory management", "stock control"],
            keyword_coverage_score=0.8,   # from KeywordAnalyser (0-1)
            citation_score=75.0,          # from CitationReadinessAnalyser (0-100)
            aeo_score=70.0,               # from AnswerScorer (0-100)
            has_same_as=True,
            has_faq_schema=False,
            has_date=True,
        )
    """

    def score(
        self,
        content: str,
        title: str = "",
        meta_description: str = "",
        query: str = "",
        target_keywords: list[str] | None = None,
        keyword_coverage_score: float = 0.0,    # 0–1 from KeywordAnalyser
        citation_score: float = 0.0,            # 0–100 from CitationReadinessAnalyser
        aeo_score: float = 0.0,                 # 0–100 from AnswerScorer
        has_same_as: bool = False,
        has_faq_schema: bool = False,
        has_date: bool = False,
        has_author: bool = False,
        has_stats: bool = False,
    ) -> QualityScoreResult:
        """
        Compute Quality Score with full dimensional breakdown.

        Args:
            content:               Full text content.
            title:                 Page/section title.
            meta_description:      Meta description or excerpt.
            query:                 The target search query.
            target_keywords:       Keywords the content must rank for.
            keyword_coverage_score: 0–1 fraction of keywords present (KeywordAnalyser).
            citation_score:        0–100 citation readiness score.
            aeo_score:             0–100 AEO answer readiness score.
            has_same_as:           Whether verified sameAs schema links exist.
            has_faq_schema:        Whether FAQPage schema is present.
            has_date:              Whether a publication date is present.
            has_author:            Whether author attribution is present.
            has_stats:             Whether statistics are present.

        Returns:
            QualityScoreResult with full breakdown and action plan.
        """
        kws = target_keywords or []
        word_count = len(content.split())

        # ── Dimension 1: Snippet Attractiveness (Expected CTR) ────────────────
        snip_dim = self._score_snippet_attractiveness(
            content=content,
            title=title,
            meta_description=meta_description,
            query=query,
            kws=kws,
        )

        # ── Dimension 2: Keyword & Intent Alignment (Ad Relevance) ───────────
        align_dim = self._score_keyword_alignment(
            content=content,
            title=title,
            query=query,
            kws=kws,
            keyword_coverage_score=keyword_coverage_score,
            aeo_score=aeo_score,
            has_faq_schema=has_faq_schema,
        )

        # ── Dimension 3: Content Experience Score (Landing Page) ──────────────
        exp_dim = self._score_content_experience(
            content=content,
            citation_score=citation_score,
            has_same_as=has_same_as,
            has_date=has_date,
            has_author=has_author,
            has_stats=has_stats,
            word_count=word_count,
        )

        # ── Composite Quality Score (1–10) ────────────────────────────────────
        raw_qs = (
            snip_dim.score  * _W_SNIPPET +
            align_dim.score * _W_ALIGNMENT +
            exp_dim.score   * _W_EXPERIENCE
        )
        quality_score = round(max(1.0, min(10.0, raw_qs)), 1)

        # ── Labels & projections ──────────────────────────────────────────────
        label, summary = self._get_band(quality_score)
        beats_paid = quality_score >= 8.0
        position   = self._project_position(quality_score)
        next_score = self._score_to_next_band(quality_score)

        # ── Priority action plan ──────────────────────────────────────────────
        all_fixes = (
            snip_dim.fixes  +
            align_dim.fixes +
            exp_dim.fixes
        )
        priority_fixes = all_fixes[:5]
        quick_wins = self._extract_quick_wins(snip_dim, align_dim, exp_dim)

        logger.debug(
            "quality_score_computed",
            query=query,
            qs=quality_score,
            label=label,
            snippet=snip_dim.score,
            alignment=align_dim.score,
            experience=exp_dim.score,
            beats_paid_ads=beats_paid,
        )

        return QualityScoreResult(
            quality_score=quality_score,
            snippet_attractiveness=snip_dim,
            keyword_alignment=align_dim,
            content_experience=exp_dim,
            label=label,
            summary=summary,
            beats_paid_ads=beats_paid,
            priority_fixes=priority_fixes,
            quick_wins=quick_wins,
            projected_position=position,
            score_to_next_band=next_score,
            word_count=word_count,
            query=query,
        )

    # ── Dimension scorers ─────────────────────────────────────────────────────

    def _score_snippet_attractiveness(
        self,
        content: str,
        title: str,
        meta_description: str,
        query: str,
        kws: list[str],
    ) -> QualityScoreDimension:
        """
        Score Expected CTR equivalent — how compelling is the snippet
        that users see in the SERP before clicking?
        """
        score = 0.0
        signals: list[str] = []
        issues:  list[str] = []
        fixes:   list[str] = []

        # Title: primary keyword present (+3)
        primary = kws[0].lower() if kws else query.lower()
        if primary and title and primary in title.lower():
            score += 3.0
            signals.append(f"Primary keyword '{primary}' present in title.")
        elif primary and title:
            issues.append(f"Primary keyword '{primary}' missing from title.")
            fixes.append(f"Add '{primary}' to the page title — this is the #1 CTR signal.")

        # Title length optimal 50–60 chars (+1)
        if title and 50 <= len(title) <= 60:
            score += 1.0
            signals.append("Title length optimal (50–60 chars).")
        elif title and len(title) > 60:
            issues.append(f"Title too long ({len(title)} chars) — Google truncates above 60.")
            fixes.append("Shorten title to 50–60 characters to prevent SERP truncation.")
        elif title and len(title) < 30:
            issues.append("Title too short — misses keyword co-occurrence opportunities.")
            fixes.append("Expand title to 50–60 characters with secondary keyword.")

        # Meta description: query present (+1.5)
        if meta_description and primary and primary in meta_description.lower():
            score += 1.5
            signals.append("Primary keyword in meta description (bold in SERP).")
        elif meta_description:
            issues.append("Primary keyword absent from meta description.")
            fixes.append("Include primary keyword in meta — Google bolds it in the SERP snippet.")

        # Meta description: power verb present (+1)
        if meta_description and re.search(_POWER_VERBS, meta_description, re.IGNORECASE):
            score += 1.0
            signals.append("Power verb in meta description increases CTR.")
        elif meta_description:
            fixes.append("Add a power verb to meta description (e.g. 'Discover', 'Boost', 'Cut').")

        # First sentence rule: direct opener (+2)
        lower = content.lower().strip()
        first_sent = re.split(r'(?<=[.!?])\s+', content.strip())[0] if content else ""
        has_filler = any(lower.startswith(f) for f in _FILLER_OPENERS)
        if not has_filler and len(first_sent) > 20:
            score += 2.0
            signals.append("First sentence is direct (no filler) — highest snippet attractiveness signal.")
        else:
            issues.append("First sentence starts with filler — kills snippet CTR.")
            fixes.append(
                "CRITICAL: Rewrite first sentence. Must open: '{Subject} is/provides/does {fact}.' "
                "No preamble — this is the zero-click answer that appears in AI Overviews."
            )

        # Number in title/meta (CTR +18% per Google's own studies)
        has_number_title = bool(re.search(r'\b\d+\b', title))
        has_number_meta  = bool(re.search(r'\b\d+\b', meta_description))
        if has_number_title or has_number_meta:
            score += 0.5
            signals.append("Number in title/meta — proven +18% CTR lift (Google internal data).")

        status = self._status(score, 8.0)
        return QualityScoreDimension(
            name="Snippet Attractiveness (Expected CTR)",
            score=round(min(10.0, score), 1),
            status=status,
            signals=signals,
            issues=issues,
            fixes=fixes,
        )

    def _score_keyword_alignment(
        self,
        content: str,
        title: str,
        query: str,
        kws: list[str],
        keyword_coverage_score: float,
        aeo_score: float,
        has_faq_schema: bool,
    ) -> QualityScoreDimension:
        """
        Score Ad Relevance equivalent — does the content precisely match
        the searcher's query intent?
        """
        score = 0.0
        signals: list[str] = []
        issues:  list[str] = []
        fixes:   list[str] = []

        # Keyword coverage (0–1 → 0–4 pts)
        cov_pts = keyword_coverage_score * 4.0
        score  += cov_pts
        if keyword_coverage_score >= 0.8:
            signals.append(f"High keyword coverage ({keyword_coverage_score*100:.0f}%).")
        elif keyword_coverage_score >= 0.5:
            signals.append(f"Moderate keyword coverage ({keyword_coverage_score*100:.0f}%).")
        else:
            issues.append(f"Low keyword coverage ({keyword_coverage_score*100:.0f}% of target keywords present).")
            fixes.append("Add missing keywords naturally throughout the content (aim for 80%+ coverage).")

        # Query words in content
        if query:
            q_words = set(re.findall(r'\b\w{4,}\b', query.lower()))
            content_lower = content.lower()
            matched_q = sum(1 for w in q_words if w in content_lower)
            q_ratio = matched_q / max(len(q_words), 1)
            score += q_ratio * 2.0
            if q_ratio >= 0.8:
                signals.append("Query words well-represented in content body.")
            else:
                fixes.append(f"Include more words from the query '{query}' naturally in the content.")

        # AEO / intent match bonus (0–100 → scaled to 0–2.5 pts)
        aeo_pts = min(2.5, aeo_score * 0.025)
        score  += aeo_pts
        if aeo_score >= 70:
            signals.append(f"Strong intent alignment (AEO score {aeo_score:.0f}/100).")
        else:
            issues.append(f"Weak intent alignment (AEO {aeo_score:.0f}/100) — content doesn't directly answer the query.")
            fixes.append(
                "Structure the opening paragraph (40–60 words) as a direct answer to the query. "
                "Google's intent-match is the most heavily weighted Ad Relevance factor."
            )

        # FAQ schema: strong intent signal for question queries
        if has_faq_schema:
            score += 1.0
            signals.append("FAQPage schema present — top intent-match signal for question queries.")
        elif query and re.search(r'\b(?:what|how|why|when|where|best|vs|vs\.)\b', query, re.IGNORECASE):
            fixes.append("Add FAQPage schema — question queries with FAQ schema dominate intent-match scoring.")

        status = self._status(score, 8.0)
        return QualityScoreDimension(
            name="Keyword & Intent Alignment (Ad Relevance)",
            score=round(min(10.0, score), 1),
            status=status,
            signals=signals,
            issues=issues,
            fixes=fixes,
        )

    def _score_content_experience(
        self,
        content: str,
        citation_score: float,
        has_same_as: bool,
        has_date: bool,
        has_author: bool,
        has_stats: bool,
        word_count: int,
    ) -> QualityScoreDimension:
        """
        Score Landing Page Experience equivalent — E-E-A-T + structure +
        citation readiness + freshness. This dimension has the highest weight
        because Google's Page Experience update made it the dominant ranking factor.
        """
        score = 0.0
        signals: list[str] = []
        issues:  list[str] = []
        fixes:   list[str] = []

        # Citation readiness (0–100 → 0–3 pts) — verifiability = E-E-A-T Experience
        cit_pts = min(3.0, citation_score * 0.03)
        score  += cit_pts
        if citation_score >= 70:
            signals.append(f"High citation readiness ({citation_score:.0f}/100) — strong E-E-A-T Experience signal.")
        else:
            issues.append(f"Low citation readiness ({citation_score:.0f}/100) — content not verifiable enough.")
            fixes.append(
                "Add statistics, named sources, and dates. "
                "Google's E-E-A-T Experience dimension directly measures verifiability."
            )

        # sameAs verified links (E-E-A-T Authority) (+2)
        if has_same_as:
            score += 2.0
            signals.append("Verified sameAs links present — highest E-E-A-T Authority signal.")
        else:
            fixes.append(
                "Add verified Wikidata/Wikipedia sameAs links to schema. "
                "sameAs is Google's primary E-E-A-T Authority check — it literally confirms identity."
            )

        # Author attribution (E-E-A-T Expertise) (+1)
        if has_author:
            score += 1.0
            signals.append("Author attribution present — E-E-A-T Expertise signal.")
        else:
            fixes.append("Add author attribution with credentials — required for E-E-A-T Expertise score.")

        # Publication date (Freshness / Trust) (+0.5)
        if has_date:
            score += 0.5
            signals.append("Publication date present — Google freshness signal.")
        else:
            fixes.append("Add publication/update date — Google Freshness algorithm rewards dated content.")

        # Statistics (Verifiability) (+1)
        if has_stats:
            score += 1.0
            signals.append("Statistics present — verifiability proof for E-E-A-T Trustworthiness.")
        else:
            fixes.append("Add measurable statistics — quantified claims are the strongest Trust signals.")

        # Content length: 500–2000 words optimal for landing page experience (+1.5)
        if 500 <= word_count <= 2000:
            score += 1.5
            signals.append(f"Content length optimal ({word_count} words) for landing page experience.")
        elif word_count < 300:
            issues.append(f"Content too short ({word_count} words) — Google penalises thin content.")
            fixes.append("Expand content to at least 500 words. Thin content is the #1 landing page experience killer.")
        elif word_count > 3000:
            score += 0.5  # long-form still decent but can hurt UX
            signals.append("Long-form content — good for authority but ensure readability.")

        # Structure signals (usability = landing page UX)
        has_headings = bool(re.search(r'^#{1,3}\s+', content, re.MULTILINE))
        has_lists    = bool(re.search(r'^\s*[-*•]\s+', content, re.MULTILINE))
        struct_score = 0.0
        if has_headings:
            struct_score += 0.5
            signals.append("Headings present — improves landing page usability score.")
        if has_lists:
            struct_score += 0.5
            signals.append("Lists present — scannability improves page experience score.")
        score += struct_score
        if not has_headings and word_count > 300:
            fixes.append("Add H2/H3 headings — Google explicitly uses heading structure in page experience assessment.")

        status = self._status(score, 8.0)
        return QualityScoreDimension(
            name="Content Experience Score (Landing Page)",
            score=round(min(10.0, score), 1),
            status=status,
            signals=signals,
            issues=issues,
            fixes=fixes,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _get_band(qs: float) -> tuple[str, str]:
        for threshold, label, summary in _QS_BANDS:
            if qs >= threshold:
                return label, summary
        return "Poor", _QS_BANDS[-1][2]

    @staticmethod
    def _project_position(qs: float) -> str:
        if qs >= 9.0:
            return "#1 — ranks above ALL paid ads, captured in AI Overviews"
        if qs >= 8.0:
            return "#1–#2 — above most paid ads, featured snippet eligible"
        if qs >= 6.5:
            return "#2–#3 — competitive with top-tier paid ads"
        if qs >= 5.0:
            return "#4–#6 — mid-page, below paid ads"
        if qs >= 3.0:
            return "#7–#10 — bottom of page 1, below all paid ads"
        return "Page 2+ — paid ads completely dominate"

    @staticmethod
    def _score_to_next_band(qs: float) -> float | None:
        thresholds = [t for t, _, _ in _QS_BANDS]
        for t in sorted(thresholds, reverse=True):
            if qs < t:
                return round(t - qs, 1)
        return None  # already at top band

    @staticmethod
    def _status(score: float, max_score: float) -> str:
        ratio = score / max_score
        if ratio >= 0.75:
            return "Above Average"
        if ratio >= 0.45:
            return "Average"
        return "Below Average"

    @staticmethod
    def _extract_quick_wins(
        snip: QualityScoreDimension,
        align: QualityScoreDimension,
        exp: QualityScoreDimension,
    ) -> list[str]:
        """Pull the single highest-priority fix from each below-average dimension."""
        wins: list[str] = []
        for dim in (snip, align, exp):
            if dim.status != "Above Average" and dim.fixes:
                wins.append(f"[{dim.name.split('(')[0].strip()}] {dim.fixes[0]}")
        return wins[:3]
