"""
DATA ENGINE — AEO Answer Scorer
Scores how well content functions as a direct answer for answer engines.

Answer engines (Google Featured Snippets, Bing Answers, Siri, Alexa,
ChatGPT, Perplexity) extract and display answers that match specific
quality signals. This module scores content against those signals.

Scoring dimensions (25 pts each = 100 total):
  1. Directness     — answer starts with a direct response, no fluff
  2. Completeness   — covers who/what/when/where/why/how
  3. Conciseness    — optimal length for snippet extraction (40–60 words)
  4. Verifiability  — statistics, dates, named sources present
"""

import re
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)

# Optimal answer lengths for different snippet types (words)
PARAGRAPH_SNIPPET_MIN   = 40
PARAGRAPH_SNIPPET_MAX   = 60
LIST_SNIPPET_MIN_ITEMS  = 4
LIST_SNIPPET_MAX_ITEMS  = 8
TABLE_MIN_ROWS          = 3

# Filler openers that reduce directness score
FILLER_OPENERS = (
    "in this article", "in this post", "in this guide",
    "today we will", "today we are", "we will explore",
    "it is important to note", "it should be noted",
    "there are many", "many people", "some people",
    "as we all know", "needless to say",
    "the purpose of this", "this article will",
    "in order to understand", "before we dive",
)

# Direct answer starters — high directness signal
DIRECT_STARTERS = (
    "a ", "an ", "the ", "yes", "no",
    "to ", "it ", "this ", "there ",
)


@dataclass
class AnswerScoreResult:
    """Comprehensive answer quality score for a content block."""
    overall_score: float            # 0–100
    directness_score: float         # 0–25
    completeness_score: float       # 0–25
    conciseness_score: float        # 0–25
    verifiability_score: float      # 0–25

    answer_type: str                # paragraph | list | table | definition | steps
    word_count: int
    starts_directly: bool
    has_filler_opener: bool
    completeness_signals: list[str] # which W-questions are covered
    has_statistics: bool
    has_dates: bool
    has_named_sources: bool

    snippet_ready: bool             # True if ready for featured snippet extraction
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


class AnswerScorer:
    """
    Scores a content block for answer engine extraction quality.
    Used by the AEO optimiser to identify and improve answer blocks.
    """

    def score(self, content: str, question: str = "") -> AnswerScoreResult:
        """
        Score a content block as an answer.

        Args:
            content:  The text block to score (paragraph, list, or table).
            question: The question this content answers (improves scoring accuracy).

        Returns:
            AnswerScoreResult with full dimensional breakdown.
        """
        words       = content.split()
        word_count  = len(words)
        lower       = content.lower().strip()
        first_sent  = re.split(r'(?<=[.!?])\s+', content.strip())[0] if content else ""

        answer_type = self._detect_answer_type(content)

        # Dimension 1: Directness (0–25)
        has_filler   = any(lower.startswith(f) for f in FILLER_OPENERS)
        starts_direct = any(first_sent.lower().startswith(s) for s in DIRECT_STARTERS)
        directness_score = self._score_directness(
            first_sent, has_filler, starts_direct, question
        )

        # Dimension 2: Completeness (0–25)
        completeness_signals, completeness_score = self._score_completeness(
            content, question
        )

        # Dimension 3: Conciseness (0–25)
        conciseness_score = self._score_conciseness(word_count, answer_type)

        # Dimension 4: Verifiability (0–25)
        has_stats, has_dates, has_sources, verifiability_score = (
            self._score_verifiability(content)
        )

        overall = (
            directness_score +
            completeness_score +
            conciseness_score +
            verifiability_score
        )

        issues, recs = self._build_issues_recs(
            has_filler, starts_direct, word_count,
            answer_type, has_stats, has_sources,
            completeness_signals, question,
        )

        snippet_ready = (
            overall >= 60
            and not has_filler
            and word_count >= PARAGRAPH_SNIPPET_MIN
        )

        return AnswerScoreResult(
            overall_score=round(overall, 1),
            directness_score=round(directness_score, 1),
            completeness_score=round(completeness_score, 1),
            conciseness_score=round(conciseness_score, 1),
            verifiability_score=round(verifiability_score, 1),
            answer_type=answer_type,
            word_count=word_count,
            starts_directly=starts_direct,
            has_filler_opener=has_filler,
            completeness_signals=completeness_signals,
            has_statistics=has_stats,
            has_dates=has_dates,
            has_named_sources=has_sources,
            snippet_ready=snippet_ready,
            issues=issues,
            recommendations=recs,
        )

    @staticmethod
    def _detect_answer_type(content: str) -> str:
        """Classify the structural type of an answer block."""
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        bullet_lines = sum(1 for l in lines if re.match(r'^[-*•]\s+', l))
        numbered_lines = sum(1 for l in lines if re.match(r'^\d+[.)]\s+', l))
        table_lines = sum(1 for l in lines if "|" in l)

        if numbered_lines >= 3:
            return "steps"
        if bullet_lines >= LIST_SNIPPET_MIN_ITEMS:
            return "list"
        if table_lines >= TABLE_MIN_ROWS:
            return "table"
        # Definition pattern: "X is Y" or "X refers to Y"
        if re.search(r'\b\w+\s+(is|are|refers to|means?|defined as)\b', content, re.IGNORECASE):
            return "definition"
        return "paragraph"

    def _score_directness(
        self, first_sent: str, has_filler: bool,
        starts_direct: bool, question: str
    ) -> float:
        score = 25.0
        if has_filler:
            score -= 15.0
        if not starts_direct:
            score -= 5.0
        # Bonus: first sentence contains key question words
        if question:
            q_words = set(re.findall(r'\b\w{5,}\b', question.lower()))
            a_words = set(re.findall(r'\b\w{5,}\b', first_sent.lower()))
            if q_words & a_words:
                score = min(25.0, score + 5.0)
        return max(0.0, score)

    @staticmethod
    def _score_completeness(
        content: str, question: str
    ) -> tuple[list[str], float]:
        """Check which W-questions the content covers."""
        lower = content.lower()
        signals: list[str] = []
        w_checks = {
            "what":  r'\b(?:is|are|means?|defined as|refers to)\b',
            "why":   r'\b(?:because|due to|reason|cause|result(?:s)? in)\b',
            "how":   r'\b(?:by|through|using|via|method|process|step[s]?)\b',
            "when":  r'\b(?:in \d{4}|since|after|before|during|date|time)\b',
            "where": r'\b(?:in|at|located|found|available|hosted)\b',
            "who":   r'\b(?:by|created|founded|developed|made|author)\b',
        }
        for w, pattern in w_checks.items():
            if re.search(pattern, lower):
                signals.append(w)

        score = min(25.0, len(signals) * 4.0)
        return signals, score

    @staticmethod
    def _score_conciseness(word_count: int, answer_type: str) -> float:
        """Score answer length against ideal ranges per answer type."""
        if answer_type == "paragraph":
            if PARAGRAPH_SNIPPET_MIN <= word_count <= PARAGRAPH_SNIPPET_MAX:
                return 25.0
            elif word_count < PARAGRAPH_SNIPPET_MIN:
                return max(0.0, 25.0 - (PARAGRAPH_SNIPPET_MIN - word_count) * 0.8)
            else:
                return max(0.0, 25.0 - (word_count - PARAGRAPH_SNIPPET_MAX) * 0.3)
        elif answer_type in ("list", "steps"):
            items = word_count // 6  # rough estimate
            if LIST_SNIPPET_MIN_ITEMS <= items <= LIST_SNIPPET_MAX_ITEMS:
                return 25.0
            return 15.0
        elif answer_type == "definition":
            if 15 <= word_count <= 45:
                return 25.0
            return 15.0
        return 15.0

    @staticmethod
    def _score_verifiability(content: str) -> tuple[bool, bool, bool, float]:
        """Score presence of statistics, dates, and named sources."""
        has_stats   = bool(re.search(
            r'\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|percent|million|billion|x|times|ms)\b',
            content, re.IGNORECASE
        ))
        has_dates   = bool(re.search(
            r'\b(?:January|February|March|April|May|June|July|August|September|'
            r'October|November|December|\d{4})\b',
            content
        ))
        has_sources = bool(re.search(
            r'(?:according to|per|reported by|study by|research from)',
            content, re.IGNORECASE
        ))
        score = 0.0
        if has_stats:   score += 10.0
        if has_dates:   score += 7.5
        if has_sources: score += 7.5
        return has_stats, has_dates, has_sources, min(25.0, score)

    @staticmethod
    def _build_issues_recs(
        has_filler: bool, starts_direct: bool, word_count: int,
        answer_type: str, has_stats: bool, has_sources: bool,
        completeness_signals: list[str], question: str,
    ) -> tuple[list[str], list[str]]:
        issues: list[str] = []
        recs:   list[str] = []

        if has_filler:
            issues.append("Answer starts with filler text — answer engines skip these.")
            recs.append("Remove opener and start with the direct answer.")
        if not starts_direct:
            issues.append("Answer does not start directly — add a topic-sentence first.")
            recs.append("Start with: '[Topic] is/does/means [answer].'")
        if answer_type == "paragraph" and word_count < PARAGRAPH_SNIPPET_MIN:
            issues.append(f"Answer is too short ({word_count} words). Minimum: {PARAGRAPH_SNIPPET_MIN}.")
            recs.append(f"Expand the answer to {PARAGRAPH_SNIPPET_MIN}–{PARAGRAPH_SNIPPET_MAX} words.")
        if answer_type == "paragraph" and word_count > PARAGRAPH_SNIPPET_MAX * 2:
            issues.append(f"Answer is too long ({word_count} words) for snippet extraction.")
            recs.append("Condense the answer paragraph to 40–60 words.")
        if not has_stats:
            recs.append("Add a specific statistic or number to improve verifiability.")
        if not has_sources:
            recs.append("Attribute the answer to a named source for citation credibility.")
        missing_w = {"what","why","how","when"} - set(completeness_signals)
        if missing_w:
            recs.append(f"Consider covering these angles: {', '.join(sorted(missing_w))}.")
        return issues, recs
