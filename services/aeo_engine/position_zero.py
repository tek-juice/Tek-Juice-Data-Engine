"""
DATA ENGINE — AEO Position Zero Analyser
Analyses content for "position zero" opportunities — the answers
displayed above organic results in Google/Bing including:

  - Featured Snippets (paragraph, list, table, steps)
  - People Also Ask (PAA) boxes
  - Knowledge Panels
  - Answer Boxes (direct answers from Bing/Google)
  - AI Overviews (Google SGE)
  - Perplexity Answer Cards

Position zero is the highest-value real estate in search results.
This module identifies which position-zero type each content block
is best suited for and scores its readiness.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

import structlog

logger = structlog.get_logger(__name__)

PositionZeroType = Literal[
    "featured_snippet_paragraph",
    "featured_snippet_list",
    "featured_snippet_table",
    "featured_snippet_steps",
    "people_also_ask",
    "knowledge_panel",
    "answer_box",
    "ai_overview",
    "none",
]

# PAA questions are usually 4–8 words
PAA_MIN_WORDS = 4
PAA_MAX_WORDS = 8

# Knowledge Panel triggers — entity + authority signals
KNOWLEDGE_PANEL_TRIGGERS = [
    r'\b(founded in|established in|created in)\s+\d{4}\b',
    r'\b(headquartered in|based in|located in)\b',
    r'\b(ceo|founder|president|chairman)\b',
    r'\bwikidata\b|\bwikipedia\b',
    r'\bofficial website\b',
]

# Answer box triggers — direct fact questions
ANSWER_BOX_TRIGGERS = [
    r'^(what|who|when|where|how many|how much) (is|are|was|were|does|do)\b',
    r'\b(capital of|population of|height of|age of|founder of)\b',
    r'\b\d+\s+(meters?|feet|kilometres?|miles?|years? old)\b',
]


@dataclass
class PositionZeroOpportunity:
    """A specific position-zero opportunity identified in content."""
    position_zero_type: PositionZeroType
    content_block: str
    readiness_score: float      # 0–100
    required_changes: list[str]
    estimated_word_count: int
    trigger_found: str | None


@dataclass
class PositionZeroAnalysis:
    """Complete position-zero analysis for a document."""
    opportunities: list[PositionZeroOpportunity]
    best_opportunity: PositionZeroOpportunity | None
    total_opportunities: int
    high_readiness_count: int   # score >= 70
    type_breakdown: dict[str, int]
    people_also_ask_questions: list[str]
    knowledge_panel_signals: list[str]
    overall_pz_score: float     # 0–100
    recommendations: list[str]


class PositionZeroAnalyser:
    """
    Scans content for position-zero opportunities across all answer
    box types and scores readiness for each.
    """

    def analyse(self, content: str, title: str = "", url: str = "") -> PositionZeroAnalysis:
        """
        Identify all position-zero opportunities in content.

        Args:
            content: Full plain-text document.
            title:   Page title (used for knowledge panel detection).
            url:     Page URL (used for answer box detection).

        Returns:
            PositionZeroAnalysis with all opportunities ranked by readiness.
        """
        opportunities: list[PositionZeroOpportunity] = []
        paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip().split()) >= 5]

        for para in paragraphs:
            opp = self._classify_opportunity(para)
            if opp:
                opportunities.append(opp)

        # Sort by readiness score
        opportunities.sort(key=lambda o: o.readiness_score, reverse=True)

        # PAA extraction
        paa_questions = self._extract_paa_questions(content)

        # Knowledge panel signals
        kp_signals = self._extract_knowledge_panel_signals(content, title)

        best = opportunities[0] if opportunities else None
        high_count = sum(1 for o in opportunities if o.readiness_score >= 70)
        type_breakdown: dict[str, int] = {}
        for o in opportunities:
            type_breakdown[o.position_zero_type] = type_breakdown.get(o.position_zero_type, 0) + 1

        overall = opportunities[0].readiness_score if opportunities else 0.0
        recs = self._generate_recommendations(opportunities, paa_questions, kp_signals, content)

        logger.debug(
            "position_zero_analysis_complete",
            opportunities=len(opportunities),
            paa_count=len(paa_questions),
            kp_signals=len(kp_signals),
        )

        return PositionZeroAnalysis(
            opportunities=opportunities[:10],
            best_opportunity=best,
            total_opportunities=len(opportunities),
            high_readiness_count=high_count,
            type_breakdown=type_breakdown,
            people_also_ask_questions=paa_questions,
            knowledge_panel_signals=kp_signals,
            overall_pz_score=round(overall, 1),
            recommendations=recs,
        )

    def _classify_opportunity(self, block: str) -> PositionZeroOpportunity | None:
        """Classify a content block into the most fitting position-zero type."""
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        word_count = len(block.split())
        lower = block.lower()

        # Check for steps
        numbered = sum(1 for l in lines if re.match(r'^\d+[.)]\s+', l))
        if numbered >= 3:
            score, changes = self._score_steps(block, numbered)
            return PositionZeroOpportunity(
                position_zero_type="featured_snippet_steps",
                content_block=block,
                readiness_score=score,
                required_changes=changes,
                estimated_word_count=word_count,
                trigger_found="numbered_list",
            )

        # Check for list
        bullets = sum(1 for l in lines if re.match(r'^[-*•]\s+', l))
        if bullets >= 3:
            score, changes = self._score_list(block, bullets)
            return PositionZeroOpportunity(
                position_zero_type="featured_snippet_list",
                content_block=block,
                readiness_score=score,
                required_changes=changes,
                estimated_word_count=word_count,
                trigger_found="bullet_list",
            )

        # Check for table
        pipes = sum(1 for l in lines if "|" in l)
        if pipes >= 3:
            return PositionZeroOpportunity(
                position_zero_type="featured_snippet_table",
                content_block=block,
                readiness_score=75.0,
                required_changes=["Ensure first row contains clear column headers."],
                estimated_word_count=word_count,
                trigger_found="table",
            )

        # Check for answer box
        for trigger in ANSWER_BOX_TRIGGERS:
            if re.search(trigger, lower):
                score, changes = self._score_answer_box(block, word_count)
                return PositionZeroOpportunity(
                    position_zero_type="answer_box",
                    content_block=block,
                    readiness_score=score,
                    required_changes=changes,
                    estimated_word_count=word_count,
                    trigger_found=trigger,
                )

        # Check for knowledge panel
        kp_hits = [t for t in KNOWLEDGE_PANEL_TRIGGERS if re.search(t, lower)]
        if len(kp_hits) >= 2:
            return PositionZeroOpportunity(
                position_zero_type="knowledge_panel",
                content_block=block,
                readiness_score=65.0,
                required_changes=["Add Schema.org Organisation/Person JSON-LD with sameAs links."],
                estimated_word_count=word_count,
                trigger_found=kp_hits[0],
            )

        # Default: paragraph snippet
        if 40 <= word_count <= 60:
            score, changes = self._score_paragraph_snippet(block, word_count)
            return PositionZeroOpportunity(
                position_zero_type="featured_snippet_paragraph",
                content_block=block,
                readiness_score=score,
                required_changes=changes,
                estimated_word_count=word_count,
                trigger_found=None,
            )

        return None

    @staticmethod
    def _score_steps(block: str, step_count: int) -> tuple[float, list[str]]:
        score = 50.0
        changes: list[str] = []
        if step_count >= 4:
            score += 25.0
        if step_count > 8:
            score -= 10.0
            changes.append("Reduce steps to 4–8 for optimal list snippet.")
        # Check for imperative verbs (click, open, select, go to)
        if re.search(r'\b(?:click|open|select|go to|navigate|enter|type|press)\b', block, re.IGNORECASE):
            score += 15.0
        else:
            changes.append("Start each step with an imperative verb (Click, Select, Enter).")
        return min(100.0, score), changes

    @staticmethod
    def _score_list(block: str, bullet_count: int) -> tuple[float, list[str]]:
        score = 50.0
        changes: list[str] = []
        if 4 <= bullet_count <= 8:
            score += 30.0
        elif bullet_count > 8:
            changes.append("Too many bullets — limit to 4–8 for list snippet.")
        else:
            changes.append("Add more bullets — minimum 4 for list snippet.")
        return min(100.0, score), changes

    @staticmethod
    def _score_answer_box(block: str, word_count: int) -> tuple[float, list[str]]:
        score = 40.0
        changes: list[str] = []
        # Answer boxes prefer very short, factual answers (< 30 words)
        if word_count <= 30:
            score += 40.0
        elif word_count <= 50:
            score += 20.0
            changes.append("Shorten to < 30 words for answer box extraction.")
        else:
            changes.append(f"Too long ({word_count}w) for answer box. Reduce to under 30 words.")
        return min(100.0, score), changes

    @staticmethod
    def _score_paragraph_snippet(block: str, word_count: int) -> tuple[float, list[str]]:
        score = 60.0 if 40 <= word_count <= 60 else 30.0
        changes: list[str] = []
        first_sent = re.split(r'(?<=[.!?])\s+', block.strip())[0].lower()
        if any(first_sent.startswith(f) for f in ("in this", "today", "this article")):
            score -= 20.0
            changes.append("Remove filler opener and start with a direct answer.")
        return max(0.0, min(100.0, score)), changes

    @staticmethod
    def _extract_paa_questions(content: str) -> list[str]:
        """Extract questions that could appear in People Also Ask boxes."""
        questions: list[str] = []
        sentences = re.split(r'(?<=[.!?])\s+', content)
        for sent in sentences:
            words = sent.strip().split()
            if (
                sent.strip().endswith("?")
                and PAA_MIN_WORDS <= len(words) <= PAA_MAX_WORDS
            ):
                questions.append(sent.strip())
        return questions[:10]

    @staticmethod
    def _extract_knowledge_panel_signals(content: str, title: str) -> list[str]:
        """Identify entity signals that could trigger a knowledge panel."""
        signals: list[str] = []
        lower = (content + " " + title).lower()
        for trigger in KNOWLEDGE_PANEL_TRIGGERS:
            matches = re.findall(trigger, lower)
            if matches:
                signals.extend(matches[:2])
        return list(set(signals))[:10]

    @staticmethod
    def _generate_recommendations(
        opportunities: list[PositionZeroOpportunity],
        paa_questions: list[str],
        kp_signals: list[str],
        content: str,
    ) -> list[str]:
        recs: list[str] = []
        if not opportunities:
            recs.append(
                "No position-zero opportunities found. "
                "Add a 40–60 word direct-answer paragraph and a numbered steps list."
            )
        if not paa_questions:
            recs.append(
                "No People Also Ask candidates found. "
                "Add 4–8 word questions (e.g. 'What is X?', 'How does Y work?')."
            )
        if not kp_signals:
            recs.append(
                "No knowledge panel signals detected. "
                "Add founding date, location, and Schema.org Organisation markup."
            )
        high = [o for o in opportunities if o.readiness_score >= 70]
        if not high:
            recs.append(
                "No high-readiness (70+) position-zero blocks. "
                "Improve your best candidate by fixing its required_changes."
            )
        return recs
