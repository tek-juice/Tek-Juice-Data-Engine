"""
DATA ENGINE — AEO Voice Search Optimiser
Analyses and optimises content for voice search queries from
Siri, Google Assistant, Alexa, Cortana, and voice-enabled AI.

Voice search differs from text search in 3 key ways:
  1. Conversational phrasing  — "How do I..." not "best way"
  2. Longer queries           — average 29 words vs 3 for text
  3. Local intent             — "near me" and location context
  4. Immediate answers        — expects a 1–2 sentence spoken response

This module scores and optimises content for voice search extraction.
"""

import re
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)

# Voice assistants read answers aloud — optimal spoken response: 25–30 words
VOICE_ANSWER_MIN_WORDS = 20
VOICE_ANSWER_MAX_WORDS = 30

# Conversational question patterns (typical voice phrasing)
VOICE_QUESTION_PATTERNS = [
    r"what('s| is) the (?:best|easiest|fastest|cheapest)",
    r"how (do|can|should) (i|we|you)",
    r"where (can|do|should) (i|we|you)",
    r"when (should|do|does|is|are)",
    r"why (is|are|do|does|should)",
    r"who (is|are|was|were|made|created|founded)",
    r"tell me (how|what|why|when|where|who)",
    r"what('s| is) the (difference|meaning|definition)",
    r"how (much|many|long|far|often)",
]

# Filler words that hurt spoken readability
SPOKEN_FILLER = [
    "furthermore", "nevertheless", "notwithstanding", "heretofore",
    "aforementioned", "herein", "thereof", "whereby", "wherein",
]

# Local intent markers
LOCAL_INTENT_PATTERNS = [
    r'near me', r'nearby', r'in my area', r'local',
    r'in [A-Z][a-z]+',  # "in London", "in New York"
    r'closest', r'nearest',
]


@dataclass
class VoiceSearchScore:
    """Voice search optimisation score for a content block."""
    overall_score: float            # 0–100
    conversational_score: float     # 0–25: natural spoken language
    answer_length_score: float      # 0–25: fits spoken response length
    question_alignment_score: float # 0–25: matches voice question patterns
    local_intent_score: float       # 0–25: addresses location context

    word_count: int
    is_conversational: bool
    detected_voice_patterns: list[str]
    has_local_intent: bool
    has_filler_words: list[str]

    spoken_answer: str              # extracted 25-word spoken response
    optimised_for_voice: str        # rewritten for voice delivery
    recommendations: list[str] = field(default_factory=list)


class VoiceSearchOptimiser:
    """
    Analyses and optimises content blocks for voice search extraction.
    Returns a spoken-length answer and an optimised version.
    """

    def analyse(self, content: str, question: str = "") -> VoiceSearchScore:
        """
        Score content for voice search optimisation.

        Args:
            content:  Content block to analyse.
            question: The voice query this content should answer.

        Returns:
            VoiceSearchScore with spoken answer and recommendations.
        """
        lower = content.lower()
        words = content.split()
        word_count = len(words)

        # Detect voice question patterns
        detected_patterns = [
            p for p in VOICE_QUESTION_PATTERNS
            if re.search(p, lower)
        ]

        # Detect filler words
        filler_found = [f for f in SPOKEN_FILLER if f in lower]

        # Local intent
        has_local = any(re.search(p, lower) for p in LOCAL_INTENT_PATTERNS)

        is_conversational = self._is_conversational(content)

        # Score dimensions
        conv_score   = self._score_conversational(is_conversational, filler_found)
        length_score = self._score_length(word_count)
        align_score  = self._score_question_alignment(detected_patterns, question, lower)
        local_score  = 25.0 if has_local else 10.0

        overall = conv_score + length_score + align_score + local_score

        spoken_answer   = self._extract_spoken_answer(content)
        optimised       = self._optimise_for_voice(content, spoken_answer, filler_found)
        recs            = self._generate_recommendations(
            is_conversational, word_count, detected_patterns, has_local, filler_found
        )

        logger.debug("voice_search_scored", score=round(overall, 1), word_count=word_count)

        return VoiceSearchScore(
            overall_score=round(overall, 1),
            conversational_score=round(conv_score, 1),
            answer_length_score=round(length_score, 1),
            question_alignment_score=round(align_score, 1),
            local_intent_score=round(local_score, 1),
            word_count=word_count,
            is_conversational=is_conversational,
            detected_voice_patterns=detected_patterns,
            has_local_intent=has_local,
            has_filler_words=filler_found,
            spoken_answer=spoken_answer,
            optimised_for_voice=optimised,
            recommendations=recs,
        )

    @staticmethod
    def _is_conversational(content: str) -> bool:
        """Check if content uses conversational language."""
        lower = content.lower()
        conversational_indicators = [
            r"\byou\b", r"\byour\b", r"\bwe\b", r"\bour\b",
            r"\blet's\b", r"\bhere's\b", r"\bthat's\b",
            r"\bdon't\b", r"\bcan't\b", r"\bwon't\b",
        ]
        return sum(1 for p in conversational_indicators if re.search(p, lower)) >= 2

    @staticmethod
    def _score_conversational(is_conv: bool, filler: list[str]) -> float:
        score = 20.0 if is_conv else 10.0
        score -= len(filler) * 3.0
        return max(0.0, min(25.0, score))

    @staticmethod
    def _score_length(word_count: int) -> float:
        if VOICE_ANSWER_MIN_WORDS <= word_count <= VOICE_ANSWER_MAX_WORDS:
            return 25.0
        elif word_count < VOICE_ANSWER_MIN_WORDS:
            return max(0.0, 25.0 - (VOICE_ANSWER_MIN_WORDS - word_count) * 1.5)
        else:
            return max(0.0, 25.0 - (word_count - VOICE_ANSWER_MAX_WORDS) * 0.4)

    @staticmethod
    def _score_question_alignment(
        patterns: list[str], question: str, content_lower: str
    ) -> float:
        score = 0.0
        if patterns:
            score += min(15.0, len(patterns) * 5.0)
        if question:
            q_words = set(re.findall(r'\b\w{4,}\b', question.lower()))
            c_words = set(re.findall(r'\b\w{4,}\b', content_lower))
            overlap = len(q_words & c_words) / len(q_words) if q_words else 0
            score += min(10.0, overlap * 10.0)
        return min(25.0, score)

    @staticmethod
    def _extract_spoken_answer(content: str) -> str:
        """
        Extract a 20–30 word spoken response from content.
        Takes the first complete sentence(s) that fit the voice answer window.
        """
        sentences = re.split(r'(?<=[.!?])\s+', content.strip())
        result = ""
        for sent in sentences:
            candidate = (result + " " + sent).strip()
            cand_words = len(candidate.split())
            if cand_words <= VOICE_ANSWER_MAX_WORDS:
                result = candidate
            else:
                break
        return result if result else " ".join(content.split()[:VOICE_ANSWER_MAX_WORDS])

    @staticmethod
    def _optimise_for_voice(
        content: str, spoken_answer: str, filler: list[str]
    ) -> str:
        """Replace filler words and return the voice-optimised answer."""
        optimised = spoken_answer
        for f in filler:
            optimised = re.sub(rf'\b{re.escape(f)}\b', '', optimised, flags=re.IGNORECASE)
        optimised = re.sub(r'\s{2,}', ' ', optimised).strip()
        return optimised

    @staticmethod
    def _generate_recommendations(
        is_conv: bool, word_count: int,
        patterns: list[str], has_local: bool,
        filler: list[str],
    ) -> list[str]:
        recs: list[str] = []
        if not is_conv:
            recs.append(
                "Use conversational language — voice assistants prefer 'you/your/we' "
                "over formal third-person phrasing."
            )
        if word_count > VOICE_ANSWER_MAX_WORDS * 2:
            recs.append(
                f"Content is too long for voice ({word_count}w). "
                f"Add a {VOICE_ANSWER_MIN_WORDS}–{VOICE_ANSWER_MAX_WORDS} word "
                "summary paragraph at the top."
            )
        if not patterns:
            recs.append(
                "No voice search patterns detected. "
                "Add 'How to', 'What is', or 'Where can' phrasing."
            )
        if filler:
            recs.append(
                f"Remove formal/academic words: {', '.join(filler[:3])}. "
                "Use plain spoken language instead."
            )
        if not has_local:
            recs.append(
                "Consider adding local context ('in [location]', 'near you') "
                "to capture 'near me' voice queries."
            )
        return recs
