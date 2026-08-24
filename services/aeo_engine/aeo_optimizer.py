"""
DATA ENGINE — AEO Master Optimiser
Orchestrates all AEO modules into a single analysis pipeline.
Produces a comprehensive AEO bundle matching the depth of the
SEO and GEO engines.

AEO (Answer Engine Optimisation) is the practice of structuring
content to be directly extracted and displayed by answer engines:
  - Google Featured Snippets & AI Overviews
  - Bing Answer Boxes & Copilot
  - Siri / Google Assistant / Alexa (voice)
  - ChatGPT / Perplexity answer cards
  - People Also Ask boxes
  - Knowledge Panels

The optimiser runs all four AEO analysis modules concurrently and
produces a unified score, action plan, and optimised content draft.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any

import structlog
from sqlalchemy import text

from configs.settings import get_settings
from services.aeo_engine.answer_scorer import AnswerScorer, AnswerScoreResult
from services.aeo_engine.question_mapper import QuestionMapper, QuestionMapResult
from services.aeo_engine.featured_snippet import FeaturedSnippetOptimiser, FeaturedSnippetAnalysis
from services.aeo_engine.voice_search import VoiceSearchOptimiser, VoiceSearchScore
from services.aeo_engine.position_zero import PositionZeroAnalyser, PositionZeroAnalysis

logger = structlog.get_logger(__name__)
settings = get_settings()


@dataclass
class AEOBundle:
    """Complete AEO analysis output for a document."""
    document_id: str
    tenant_id: str

    # Module results
    answer_score: AnswerScoreResult
    question_map: QuestionMapResult
    featured_snippet: FeaturedSnippetAnalysis
    voice_search: VoiceSearchScore
    position_zero: PositionZeroAnalysis

    # Composite scores
    overall_aeo_score: float        # 0–100 weighted composite
    answer_readiness: float         # 0–100
    voice_readiness: float          # 0–100
    snippet_readiness: float        # 0–100
    pz_readiness: float             # 0–100 position-zero readiness

    # Action plan
    priority_actions: list[str]     # top 5 most impactful actions
    quick_wins: list[str]           # changes achievable in < 30 min
    all_recommendations: list[str]

    # Optimised output
    optimised_intro: str            # rewritten intro for answer engines
    spoken_answer: str              # 25-word voice response

    # Metadata
    analysed_at: str
    word_count: int
    engine_targets: list[str]


class AEOOptimiser:
    """
    Master AEO orchestrator. Runs all analysis modules and produces
    a unified AEO bundle with prioritised action plan.

    Usage:
        optimiser = AEOOptimiser()
        bundle = await optimiser.optimise(
            document_id="uuid",
            tenant_id="uuid",
            content="...",
            title="...",
            target_questions=["What is X?", "How does Y work?"],
        )
    """

    def __init__(self) -> None:
        self._answer_scorer   = AnswerScorer()
        self._question_mapper = QuestionMapper()
        self._snippet_opt     = FeaturedSnippetOptimiser()
        self._voice_opt       = VoiceSearchOptimiser()
        self._pz_analyser     = PositionZeroAnalyser()

    async def optimise(
        self,
        document_id: str,
        tenant_id: str,
        content: str,
        title: str = "",
        url: str = "",
        target_questions: list[str] | None = None,
        engine_targets: list[str] | None = None,
    ) -> AEOBundle:
        """
        Run the complete AEO analysis pipeline.

        Args:
            document_id:       Source document UUID.
            tenant_id:         Tenant UUID.
            content:           Full plain-text document content.
            title:             Page title.
            url:               Page URL.
            target_questions:  Specific questions to optimise for.
            engine_targets:    Answer engines to target (default: all).

        Returns:
            AEOBundle with full analysis and action plan.
        """
        targets = engine_targets or [
            "google_featured_snippet",
            "google_ai_overview",
            "bing_answer_box",
            "perplexity",
            "chatgpt",
            "siri",
            "alexa",
            "people_also_ask",
        ]

        primary_question = (target_questions or [""])[0]

        # Run all modules — IO-bound so run concurrently in executor
        loop = asyncio.get_event_loop()
        (
            answer_score,
            question_map,
            snippet_analysis,
            voice_score,
            pz_analysis,
        ) = await asyncio.gather(
            loop.run_in_executor(None, self._answer_scorer.score, content, primary_question),
            loop.run_in_executor(None, self._question_mapper.map, content, document_id),
            loop.run_in_executor(None, self._snippet_opt.analyse, content),
            loop.run_in_executor(None, self._voice_opt.analyse, content, primary_question),
            loop.run_in_executor(None, self._pz_analyser.analyse, content, title, url),
        )

        # Composite scores
        answer_readiness  = answer_score.overall_score
        voice_readiness   = voice_score.overall_score
        snippet_readiness = snippet_analysis.overall_snippet_score
        pz_readiness      = pz_analysis.overall_pz_score

        overall = (
            answer_readiness  * 0.30 +
            snippet_readiness * 0.25 +
            pz_readiness      * 0.25 +
            voice_readiness   * 0.20
        )

        # Consolidate all recommendations
        all_recs = (
            answer_score.recommendations +
            question_map.recommendations +
            snippet_analysis.page_level_recommendations +
            voice_score.recommendations +
            pz_analysis.recommendations
        )
        # Deduplicate
        seen: set[str] = set()
        unique_recs: list[str] = []
        for r in all_recs:
            if r not in seen:
                seen.add(r)
                unique_recs.append(r)

        priority_actions = self._prioritise_actions(
            answer_score, question_map, snippet_analysis, voice_score, pz_analysis
        )
        quick_wins = self._identify_quick_wins(
            answer_score, snippet_analysis, voice_score
        )

        # Optimised content outputs
        spoken_answer    = voice_score.spoken_answer
        optimised_intro  = self._rewrite_intro(content, answer_score, voice_score)

        bundle = AEOBundle(
            document_id=document_id,
            tenant_id=tenant_id,
            answer_score=answer_score,
            question_map=question_map,
            featured_snippet=snippet_analysis,
            voice_search=voice_score,
            position_zero=pz_analysis,
            overall_aeo_score=round(overall, 1),
            answer_readiness=round(answer_readiness, 1),
            voice_readiness=round(voice_readiness, 1),
            snippet_readiness=round(snippet_readiness, 1),
            pz_readiness=round(pz_readiness, 1),
            priority_actions=priority_actions,
            quick_wins=quick_wins,
            all_recommendations=unique_recs[:20],
            optimised_intro=optimised_intro,
            spoken_answer=spoken_answer,
            analysed_at=datetime.now(UTC).isoformat(),
            word_count=len(content.split()),
            engine_targets=targets,
        )

        await self._persist(bundle)

        logger.info(
            "aeo_optimisation_complete",
            document_id=document_id,
            overall_score=bundle.overall_aeo_score,
            snippet_ready=snippet_analysis.has_snippet_ready_content,
            pz_opportunities=pz_analysis.total_opportunities,
            questions_mapped=question_map.total_questions,
        )
        return bundle

    @staticmethod
    def _prioritise_actions(
        answer: AnswerScoreResult,
        questions: QuestionMapResult,
        snippet: FeaturedSnippetAnalysis,
        voice: VoiceSearchScore,
        pz: PositionZeroAnalysis,
    ) -> list[str]:
        """Select the top 5 highest-impact actions across all modules."""
        actions: list[tuple[float, str]] = []

        # Highest impact: fix filler opener (affects all engines)
        if answer.has_filler_opener:
            actions.append((95.0, "Remove filler opener — rewrite intro with direct answer sentence."))

        # Featured snippet: word count fix
        if snippet.best_candidate and not snippet.has_snippet_ready_content:
            actions.append((90.0, "Add a 40–60 word paragraph that directly answers the primary question."))

        # Unanswered questions
        if questions.unanswered_count > 0:
            actions.append((85.0,
                f"Answer {questions.unanswered_count} detected unanswered question(s) "
                "with dedicated response paragraphs."
            ))

        # Position zero quick win
        if pz.best_opportunity and pz.best_opportunity.readiness_score >= 60:
            changes = pz.best_opportunity.required_changes[:1]
            if changes:
                actions.append((80.0, f"Position-zero fix: {changes[0]}"))

        # Voice search
        if voice.overall_score < 50:
            actions.append((75.0,
                "Add a 20–30 word conversational summary paragraph for voice search extraction."
            ))

        # PAA coverage
        if not pz.people_also_ask_questions:
            actions.append((70.0, "Add 3–5 concise questions (4–8 words) to capture PAA boxes."))

        actions.sort(key=lambda x: x[0], reverse=True)
        return [a[1] for a in actions[:5]]

    @staticmethod
    def _identify_quick_wins(
        answer: AnswerScoreResult,
        snippet: FeaturedSnippetAnalysis,
        voice: VoiceSearchScore,
    ) -> list[str]:
        """Identify changes achievable in under 30 minutes."""
        wins: list[str] = []

        if answer.has_filler_opener:
            wins.append("Delete first sentence filler and replace with direct answer (5 min).")

        if snippet.best_candidate and snippet.best_candidate.optimised_text != snippet.best_candidate.text:
            wins.append(
                "Trim best snippet candidate to optimal word count "
                f"({snippet.best_candidate.word_count}w → 40–60w) (10 min)."
            )

        if voice.has_filler_words:
            wins.append(
                f"Replace formal words ({', '.join(voice.has_filler_words[:2])}) "
                "with plain English (5 min)."
            )

        wins.append("Add a 'Frequently Asked Questions' H2 section with 3 direct Q&A pairs (20 min).")
        return wins[:4]

    @staticmethod
    def _rewrite_intro(
        content: str,
        answer: AnswerScoreResult,
        voice: VoiceSearchScore,
    ) -> str:
        """
        Return a rewritten first paragraph optimised for answer engines.
        Uses the voice spoken answer as the base if it's better than the original.
        """
        import re
        sentences = re.split(r'(?<=[.!?])\s+', content.strip())
        original_intro = sentences[0] if sentences else content[:200]

        # If voice answer is good, use it as the optimised intro
        if voice.overall_score >= 50 and voice.optimised_for_voice:
            return voice.optimised_for_voice

        # Otherwise return original without filler
        if answer.has_filler_opener and len(sentences) > 1:
            return sentences[1] if sentences[1] else original_intro

        return original_intro

    async def _persist(self, bundle: AEOBundle) -> None:
        """Persist AEO results to the aeo_optimisation_results table."""
        try:
            from configs.database import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                await session.execute(
                    text("""
                        INSERT INTO aeo_optimisation_results (
                            document_id, tenant_id,
                            overall_aeo_score, answer_readiness,
                            voice_readiness, snippet_readiness, pz_readiness,
                            snippet_ready, voice_answer,
                            pz_opportunities, questions_answered,
                            priority_actions, recommendations,
                            engine_targets, metadata
                        ) VALUES (
                            :doc_id, :tenant_id,
                            :overall, :answer,
                            :voice, :snippet, :pz,
                            :snippet_ready, :voice_answer,
                            :pz_opp, :q_answered,
                            :actions, :recs,
                            :targets, :meta
                        )
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "doc_id":       bundle.document_id,
                        "tenant_id":    bundle.tenant_id,
                        "overall":      bundle.overall_aeo_score,
                        "answer":       bundle.answer_readiness,
                        "voice":        bundle.voice_readiness,
                        "snippet":      bundle.snippet_readiness,
                        "pz":           bundle.pz_readiness,
                        "snippet_ready": bundle.featured_snippet.has_snippet_ready_content,
                        "voice_answer": bundle.spoken_answer,
                        "pz_opp":       bundle.position_zero.total_opportunities,
                        "q_answered":   bundle.question_map.answered_count,
                        "actions":      json.dumps(bundle.priority_actions),
                        "recs":         json.dumps(bundle.all_recommendations),
                        "targets":      json.dumps(bundle.engine_targets),
                        "meta":         json.dumps({"word_count": bundle.word_count}),
                    },
                )
                await session.commit()
                logger.debug("aeo_bundle_persisted", document_id=bundle.document_id)

        except Exception as exc:
            logger.error("aeo_bundle_persist_failed", error=str(exc))
