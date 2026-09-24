"""
DATA ENGINE — AEO Featured Snippet Optimiser
Identifies, extracts, and optimises content blocks for Google Featured
Snippets, Bing Answer Boxes, and equivalent position-zero results.

Featured snippet types and their structural requirements:
  Paragraph — 40–60 words, starts with direct definition/answer
  List      — 4–8 bullet points, each 5–10 words
  Table     — 3+ rows, clear column headers, comparative data
  Video     — timestamp-based answers (not handled here)
  Steps     — numbered list, imperative verbs, ordered procedure
"""

import re
from dataclasses import dataclass, field
from typing import Literal

import structlog

logger = structlog.get_logger(__name__)

SnippetType = Literal["paragraph", "list", "table", "steps", "definition", "none"]

# Google's confirmed optimal paragraph snippet length
SNIPPET_MIN_WORDS = 40
SNIPPET_MAX_WORDS = 60

# Trigger patterns that commonly win featured snippets
SNIPPET_TRIGGER_PATTERNS = [
    r'^what is\b',
    r'^what are\b',
    r'^how (to|do|does|can)\b',
    r'^why (is|are|does|do)\b',
    r'^(best|top) \d+\b',
    r'^definition of\b',
    r'^difference between\b',
    r'^steps? (to|for)\b',
]


@dataclass
class SnippetCandidate:
    """A content block that is a candidate for featured snippet extraction."""
    text: str
    snippet_type: SnippetType
    word_count: int
    starts_with_direct_answer: bool
    has_trigger_phrase: bool
    trigger_match: str | None
    readability_score: float    # 0–100 (Flesch approximation)
    snippet_score: float        # 0–100 composite score
    optimised_text: str         # improved version ready for snippet
    issues: list[str] = field(default_factory=list)


@dataclass
class FeaturedSnippetAnalysis:
    """Full featured snippet analysis for a document."""
    best_candidate: SnippetCandidate | None
    all_candidates: list[SnippetCandidate]
    has_snippet_ready_content: bool
    snippet_type_distribution: dict[str, int]
    overall_snippet_score: float    # 0–100
    page_level_recommendations: list[str]


class FeaturedSnippetOptimiser:
    """
    Analyses content to identify and improve featured snippet candidates.
    Scores each content block and returns the best candidate with
    an optimised version ready for direct snippet extraction.
    """

    def analyse(self, content: str) -> FeaturedSnippetAnalysis:
        """
        Find and score all featured snippet candidates in content.

        Args:
            content: Full plain-text document content.

        Returns:
            FeaturedSnippetAnalysis with ranked candidates.
        """
        # Split into candidate blocks: paragraphs and list sections
        blocks = self._split_into_blocks(content)
        candidates: list[SnippetCandidate] = []

        for block in blocks:
            if len(block.split()) < 10:
                continue
            candidate = self._score_block(block)
            candidates.append(candidate)

        # Sort by snippet score descending
        candidates.sort(key=lambda c: c.snippet_score, reverse=True)

        best = candidates[0] if candidates else None
        has_ready = any(c.snippet_score >= 70 for c in candidates)

        type_dist: dict[str, int] = {}
        for c in candidates:
            type_dist[c.snippet_type] = type_dist.get(c.snippet_type, 0) + 1

        overall = candidates[0].snippet_score if candidates else 0.0
        recs = self._page_recommendations(candidates, content)

        logger.debug(
            "featured_snippet_analysis_complete",
            candidates=len(candidates),
            best_score=round(best.snippet_score, 1) if best else 0,
        )

        return FeaturedSnippetAnalysis(
            best_candidate=best,
            all_candidates=candidates[:10],
            has_snippet_ready_content=has_ready,
            snippet_type_distribution=type_dist,
            overall_snippet_score=round(overall, 1),
            page_level_recommendations=recs,
        )

    def _score_block(self, block: str) -> SnippetCandidate:
        """Score a single content block for snippet potential."""
        snippet_type = self._classify_type(block)
        words = block.split()
        word_count = len(words)

        # Check for trigger phrase
        lower = block.lower().strip()
        trigger_match = None
        for pattern in SNIPPET_TRIGGER_PATTERNS:
            if re.search(pattern, lower):
                trigger_match = pattern
                break

        # Direct answer start
        first_sent = re.split(r'(?<=[.!?])\s+', block.strip())[0]
        starts_direct = not any(
            first_sent.lower().startswith(f)
            for f in ("in this", "today we", "this article", "we will")
        )

        readability = self._flesch_approximation(block)

        # Composite score
        score = 0.0
        if snippet_type == "paragraph":
            if SNIPPET_MIN_WORDS <= word_count <= SNIPPET_MAX_WORDS:
                score += 35.0
            elif word_count < SNIPPET_MIN_WORDS:
                score += max(0, 35 - (SNIPPET_MIN_WORDS - word_count) * 1.5)
            else:
                score += max(0, 35 - (word_count - SNIPPET_MAX_WORDS) * 0.5)
        elif snippet_type in ("list", "steps"):
            score += 30.0
        elif snippet_type == "table":
            score += 28.0
        elif snippet_type == "definition":
            score += 32.0

        if trigger_match:
            score += 20.0
        if starts_direct:
            score += 20.0
        if readability >= 60:
            score += 10.0
        elif readability >= 40:
            score += 5.0

        score = min(100.0, score)

        # Generate optimised version
        optimised = self._optimise_block(block, snippet_type, word_count)

        issues: list[str] = []
        if snippet_type == "paragraph" and word_count > SNIPPET_MAX_WORDS:
            issues.append(f"Too long ({word_count}w) — trim to {SNIPPET_MAX_WORDS}w for paragraph snippet.")
        if snippet_type == "paragraph" and word_count < SNIPPET_MIN_WORDS:
            issues.append(f"Too short ({word_count}w) — expand to {SNIPPET_MIN_WORDS}w minimum.")
        if not starts_direct:
            issues.append("Remove introductory filler and start with the direct answer.")

        return SnippetCandidate(
            text=block,
            snippet_type=snippet_type,
            word_count=word_count,
            starts_with_direct_answer=starts_direct,
            has_trigger_phrase=bool(trigger_match),
            trigger_match=trigger_match,
            readability_score=round(readability, 1),
            snippet_score=round(score, 1),
            optimised_text=optimised,
            issues=issues,
        )

    @staticmethod
    def _classify_type(block: str) -> SnippetType:
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        bullets  = sum(1 for l in lines if re.match(r'^[-*•]\s+', l))
        numbered = sum(1 for l in lines if re.match(r'^\d+[.)]\s+', l))
        pipes    = sum(1 for l in lines if "|" in l)
        if numbered >= 3:
            return "steps"
        if bullets >= 3:
            return "list"
        if pipes >= 3:
            return "table"
        if re.search(r'\b\w+\s+(is|are|means?|defined as|refers to)\b', block, re.IGNORECASE):
            return "definition"
        return "paragraph"

    @staticmethod
    def _flesch_approximation(text: str) -> float:
        """Approximate Flesch Reading Ease score (0=hard, 100=easy)."""
        sentences = re.split(r'[.!?]+', text)
        sentences = [s for s in sentences if s.strip()]
        words = text.split()
        if not sentences or not words:
            return 50.0
        # Count syllables heuristically (vowel groups)
        syllables = sum(
            max(1, len(re.findall(r'[aeiouAEIOU]+', w)))
            for w in words
        )
        asl = len(words) / len(sentences)
        asw = syllables / len(words)
        return round(206.835 - (1.015 * asl) - (84.6 * asw), 1)

    @staticmethod
    def _optimise_block(text: str, snippet_type: str, word_count: int) -> str:
        """Return an optimised version of the block for snippet extraction."""
        if snippet_type != "paragraph":
            return text  # lists/tables/steps don't need word-count trimming

        if SNIPPET_MIN_WORDS <= word_count <= SNIPPET_MAX_WORDS:
            return text  # already optimal

        if word_count > SNIPPET_MAX_WORDS:
            # Trim to first 2 sentences
            sentences = re.split(r'(?<=[.!?])\s+', text.strip())
            result = ""
            for sent in sentences:
                candidate = (result + " " + sent).strip()
                if len(candidate.split()) <= SNIPPET_MAX_WORDS:
                    result = candidate
                else:
                    break
            return result if result else text
        return text

    @staticmethod
    def _page_recommendations(
        candidates: list[SnippetCandidate], content: str
    ) -> list[str]:
        recs: list[str] = []
        if not candidates:
            recs.append("No snippet-worthy content blocks found. Add direct Q&A sections.")
            return recs
        best = candidates[0]
        if best.snippet_score < 50:
            recs.append(
                "No high-scoring snippet candidates. Add a 40–60 word paragraph "
                "that starts with a direct definition or answer."
            )
        if not any(c.snippet_type in ("list", "steps") for c in candidates):
            recs.append(
                "No list or step-based content found. "
                "Add a bulleted list or numbered steps section — "
                "these win list featured snippets."
            )
        if not any(c.has_trigger_phrase for c in candidates):
            recs.append(
                "No trigger phrases found (e.g. 'What is X', 'How to Y'). "
                "Add a heading or sentence using these patterns."
            )
        return recs
