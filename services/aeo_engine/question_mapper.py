"""
DATA ENGINE — AEO Question Mapper
Extracts and classifies questions from content, then maps them to
the query patterns used by answer engines (Google, Bing, Siri, Alexa,
ChatGPT, Perplexity).

AEO principle: content must directly answer the questions users ask.
This module identifies:
  - Explicit questions in the content
  - Implicit questions the content answers but never states
  - Question intent categories (informational, navigational, transactional)
  - Voice search question patterns (conversational, long-tail)
"""

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Question intent taxonomy ──────────────────────────────────────────────────

class QuestionIntent:
    INFORMATIONAL  = "informational"   # What is, How does, Why does
    NAVIGATIONAL   = "navigational"    # Where can I find, How to get to
    TRANSACTIONAL  = "transactional"   # How to buy, Best price for
    COMPARATIVE    = "comparative"     # What is the difference between
    PROCEDURAL     = "procedural"      # How to, Step by step
    DEFINITIONAL   = "definitional"    # What is, Define, Meaning of
    CAUSAL         = "causal"          # Why does, What causes, Reason for
    QUANTITATIVE   = "quantitative"    # How many, How much, How often


# Question word patterns → intent mapping
QUESTION_INTENT_PATTERNS: list[tuple[str, str]] = [
    (r'^what is\b',                  QuestionIntent.DEFINITIONAL),
    (r'^what are\b',                 QuestionIntent.DEFINITIONAL),
    (r'^define\b',                   QuestionIntent.DEFINITIONAL),
    (r'^what does .+ mean',          QuestionIntent.DEFINITIONAL),
    (r'^how (do|does|can|to)\b',     QuestionIntent.PROCEDURAL),
    (r'^step[s]? (to|for|by)\b',     QuestionIntent.PROCEDURAL),
    (r'^why (is|are|does|do)\b',     QuestionIntent.CAUSAL),
    (r'^what causes\b',              QuestionIntent.CAUSAL),
    (r'^what is the difference\b',   QuestionIntent.COMPARATIVE),
    (r'^(compare|vs\.?|versus)\b',   QuestionIntent.COMPARATIVE),
    (r'^how (much|many|often|long)', QuestionIntent.QUANTITATIVE),
    (r'^where (can|do|is|are)\b',    QuestionIntent.NAVIGATIONAL),
    (r'^(how to buy|best .+ for)\b', QuestionIntent.TRANSACTIONAL),
]

# Voice search trigger words — conversational phrasing
VOICE_TRIGGERS = [
    "hey siri", "ok google", "alexa", "hey google",
    "tell me", "show me", "find me", "what's the",
    "how do i", "can you", "i want to know",
]

# Common question starters
QUESTION_STARTERS = (
    "what", "how", "why", "when", "where", "who", "which",
    "can", "could", "should", "would", "is", "are", "does",
    "do", "will", "has", "have", "define", "explain", "describe",
)


@dataclass
class MappedQuestion:
    """A single question mapped to intent, voice pattern, and answer quality."""
    text: str
    intent: str
    is_voice_search: bool
    is_long_tail: bool          # > 5 words
    word_count: int
    has_direct_answer: bool     # does the content answer it directly?
    answer_excerpt: str         # the best answer found in content
    answer_position: int        # paragraph index where answer appears
    answer_quality: float       # 0.0–1.0 quality of the answer


@dataclass
class QuestionMapResult:
    """Complete question mapping output for a document."""
    explicit_questions: list[MappedQuestion]    # questions stated in content
    implicit_questions: list[MappedQuestion]    # questions implied by content
    total_questions: int
    answered_count: int
    unanswered_count: int
    intent_distribution: dict[str, int]
    voice_search_count: int
    long_tail_count: int
    coverage_score: float       # 0.0–1.0
    recommendations: list[str]


class QuestionMapper:
    """
    Extracts, classifies, and scores questions from document content.
    Maps questions to the intent patterns answer engines use to match
    user queries to authoritative answers.
    """

    def map(self, content: str, document_id: str = "") -> QuestionMapResult:
        """
        Extract and classify all questions from content.

        Args:
            content:     Full document plain text.
            document_id: Optional document ID for logging.

        Returns:
            QuestionMapResult with full question analysis.
        """
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]

        explicit   = self._extract_explicit_questions(content, paragraphs)
        implicit   = self._infer_implicit_questions(content, paragraphs)

        all_questions = explicit + implicit
        answered    = [q for q in all_questions if q.has_direct_answer]
        unanswered  = [q for q in all_questions if not q.has_direct_answer]

        intent_dist: dict[str, int] = {}
        for q in all_questions:
            intent_dist[q.intent] = intent_dist.get(q.intent, 0) + 1

        voice_count    = sum(1 for q in all_questions if q.is_voice_search)
        long_tail_count = sum(1 for q in all_questions if q.is_long_tail)
        coverage_score = len(answered) / len(all_questions) if all_questions else 0.0

        recs = self._generate_recommendations(unanswered, coverage_score)

        logger.debug(
            "question_mapping_complete",
            document_id=document_id,
            explicit=len(explicit),
            implicit=len(implicit),
            answered=len(answered),
            coverage=round(coverage_score, 3),
        )

        return QuestionMapResult(
            explicit_questions=explicit,
            implicit_questions=implicit,
            total_questions=len(all_questions),
            answered_count=len(answered),
            unanswered_count=len(unanswered),
            intent_distribution=intent_dist,
            voice_search_count=voice_count,
            long_tail_count=long_tail_count,
            coverage_score=round(coverage_score, 4),
            recommendations=recs,
        )

    def _extract_explicit_questions(
        self, content: str, paragraphs: list[str]
    ) -> list[MappedQuestion]:
        """Find sentences that are direct questions (end with ?)."""
        questions: list[MappedQuestion] = []
        # Split into sentences and find question sentences
        sentences = re.split(r'(?<=[.!?])\s+', content)
        for sent in sentences:
            sent = sent.strip()
            if not sent.endswith("?"):
                continue
            if len(sent) < 10:
                continue
            mq = self._build_mapped_question(sent, paragraphs, is_explicit=True)
            questions.append(mq)
        return questions

    def _infer_implicit_questions(
        self, content: str, paragraphs: list[str]
    ) -> list[MappedQuestion]:
        """
        Infer questions the content implicitly answers based on headings,
        topic sentences, and structural patterns.
        """
        implicit: list[MappedQuestion] = []

        # H2/H3 headings are often implied questions
        headings = re.findall(r'^#{2,3}\s+(.+)$', content, re.MULTILINE)
        for heading in headings:
            # Rephrase heading as a question if it isn't already
            question = self._heading_to_question(heading.strip())
            if question:
                mq = self._build_mapped_question(question, paragraphs, is_explicit=False)
                implicit.append(mq)

        # Bold phrases often indicate key topic answers
        bold_phrases = re.findall(r'\*\*([^*]{5,60})\*\*', content)
        for phrase in bold_phrases[:10]:
            question = f"What is {phrase}?"
            mq = self._build_mapped_question(question, paragraphs, is_explicit=False)
            implicit.append(mq)

        return implicit

    def _build_mapped_question(
        self, text: str, paragraphs: list[str], is_explicit: bool
    ) -> MappedQuestion:
        """Build a MappedQuestion by classifying and scoring it."""
        intent        = self._classify_intent(text)
        is_voice      = self._is_voice_search(text)
        word_count    = len(text.split())
        is_long_tail  = word_count > 5

        # Find the best answer paragraph
        answer_para, answer_pos, answer_quality = self._find_answer(text, paragraphs)
        has_answer    = answer_quality >= 0.3

        return MappedQuestion(
            text=text,
            intent=intent,
            is_voice_search=is_voice,
            is_long_tail=is_long_tail,
            word_count=word_count,
            has_direct_answer=has_answer,
            answer_excerpt=answer_para[:250] if answer_para else "",
            answer_position=answer_pos,
            answer_quality=round(answer_quality, 3),
        )

    @staticmethod
    def _classify_intent(question: str) -> str:
        lower = question.lower().strip()
        for pattern, intent in QUESTION_INTENT_PATTERNS:
            if re.search(pattern, lower):
                return intent
        return QuestionIntent.INFORMATIONAL

    @staticmethod
    def _is_voice_search(question: str) -> bool:
        """Heuristic: voice searches are conversational and > 5 words."""
        lower = question.lower()
        is_conversational = any(t in lower for t in VOICE_TRIGGERS)
        is_long = len(question.split()) >= 6
        starts_with_trigger = lower.startswith(QUESTION_STARTERS)
        return is_conversational or (is_long and starts_with_trigger)

    @staticmethod
    def _find_answer(question: str, paragraphs: list[str]) -> tuple[str, int, float]:
        """
        Find the paragraph that best answers a question.
        Uses keyword overlap as a lightweight proxy for relevance.
        """
        if not paragraphs:
            return "", 0, 0.0

        q_words = set(re.findall(r'\b\w{4,}\b', question.lower()))
        q_words -= {"what", "where", "when", "which", "does", "this", "that", "with"}

        best_score, best_para, best_idx = 0.0, "", 0
        for i, para in enumerate(paragraphs):
            p_words = set(re.findall(r'\b\w{4,}\b', para.lower()))
            if not q_words:
                continue
            overlap = len(q_words & p_words) / len(q_words)
            if overlap > best_score:
                best_score, best_para, best_idx = overlap, para, i

        return best_para, best_idx, best_score

    @staticmethod
    def _heading_to_question(heading: str) -> str | None:
        """Convert a heading into a question if it isn't already phrased as one."""
        if heading.endswith("?"):
            return heading
        # Headings that start with verbs → "How to" questions
        verb_starters = ("use", "build", "create", "implement", "configure", "install",
                         "run", "set up", "deploy", "optimise", "improve")
        lower = heading.lower()
        if any(lower.startswith(v) for v in verb_starters):
            return f"How to {heading}?"
        # Noun phrases → "What is" questions
        if not lower.startswith(QUESTION_STARTERS):
            return f"What is {heading}?"
        return None

    @staticmethod
    def _generate_recommendations(
        unanswered: list[MappedQuestion], coverage: float
    ) -> list[str]:
        recs = []
        if coverage < 0.5:
            recs.append(
                f"Only {coverage*100:.0f}% of detected questions have direct answers. "
                "Add explicit answer paragraphs below each question."
            )
        if unanswered:
            top = unanswered[:3]
            for q in top:
                recs.append(f"Add a direct answer to: \"{q.text}\"")
        voice = [q for q in unanswered if q.is_voice_search]
        if voice:
            recs.append(
                f"{len(voice)} unanswered voice-search questions found. "
                "Voice answers should be 1–2 concise sentences."
            )
        return recs
