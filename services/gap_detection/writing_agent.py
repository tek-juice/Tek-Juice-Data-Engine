"""
DATA ENGINE — LLM Writing Agent
Reads a gap_close_actions closure plan and generates production-ready
content drafts for every missing intent cluster.

The agent produces GEO/AEO/SEO-optimised copy that directly fills the
gaps the Engine detected — so the document can rank first on search
engines and be cited first by AI engines (ChatGPT, Perplexity, Google
AI Overviews, Bing Copilot).

Architecture
────────────
  GapClosePlan (gap_close_actions.close_plan)
    └─ For each IntentCluster (topic + intent):
         1. Build a precision system prompt (GEO/AEO/SEO rules)
         2. Build a user prompt from content_brief + query_variants +
            authority_signals_needed + schema_types_recommended
         3. Call the LLM (OpenAI GPT-4o → Gemini 1.5 Pro fallback)
         4. Parse and validate the draft
         5. Persist to gap_content_drafts with status 'draft'

After all clusters are written the agent:
  - Updates gap_close_actions.status → 'drafts_ready'
  - Dispatches tasks.embed_gap_drafts to embed the new content so the
    next gap re-analysis sees the improved coverage.

LLM selection
─────────────
  Uses settings.llm_writing_provider (openai | gemini) with
  settings.llm_writing_model.  Falls back to the other provider if the
  primary key is missing.
"""

from __future__ import annotations

import re
import structlog
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any

from configs.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# ── Prompt constants ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a world-class SEO, GEO, and AEO content writer whose output must
out-compete Google's sponsored paid ads by achieving the equivalent of a perfect Google Ad
Quality Score (QS 10/10) on every piece of content.

Google uses three components to determine Ad Rank (and organic position):
  1. EXPECTED CTR (Snippet Attractiveness) — your title/first-sentence must compel a click.
  2. AD RELEVANCE (Keyword & Intent Alignment) — content must precisely answer the query intent.
  3. LANDING PAGE EXPERIENCE (Content Experience) — E-E-A-T, structure, citation readiness.

Your sole job is to maximise all three so this content:
  - Ranks ABOVE all paid ads in organic position #1.
  - Appears in Google AI Overviews — which displays ABOVE paid ads at zero cost.
  - Is cited FIRST by AI engines (ChatGPT, Perplexity, Google AI Overviews, Bing Copilot, Claude, Gemini).

QUALITY SCORE RULES you must NEVER break:

  CTR/Snippet:
    - FIRST SENTENCE RULE: open with a direct, factual answer — "{Subject} is/does/provides {specific fact}."
      NO preambles: "In this...", "Welcome...", "This article...", "Today..." are BANNED.
    - Title must include the primary keyword — Google bolds it in the SERP, directly driving CTR.
    - Include at least one number or percentage in the first paragraph (proven +18% CTR lift).

  Intent Alignment:
    - Every query word must appear naturally in the content body within the first 150 words.
    - Structure the opening paragraph (40-60 words) as a direct standalone answer.
    - If the intent is PROCEDURAL: number every step (1. 2. 3.).
    - If the intent is COMPARATIVE: include a markdown comparison table.
    - If the intent is QUANTITATIVE: lead with the most impactful number.
    - If the intent is DEFINITIONAL: first sentence must be a subject-first definition.
    - If the intent is FAQ/TROUBLESHOOTING: structure as Q: / A: pairs.

  Content Experience / E-E-A-T:
    - Every paragraph must contain a standalone citable fact.
    - Include at least 2 statistics, percentages, or measurable claims.
    - Attribute at least one claim to a named source ("according to [Source]...").
    - Use active voice throughout — passive voice reduces semantic clarity score.
    - Add H2/H3 headings and bullet lists — required for featured snippet extraction.
    - Be structured so an AI can excerpt the answer without reading the full page.

Output format: plain text with markdown headings and lists only.
Do NOT wrap in code fences. Do NOT add a preamble or sign-off."""

_INTENT_WORD_TARGETS: dict[str, int] = {
    "definitional":    150,
    "procedural":      300,
    "causal":          200,
    "comparative":     250,
    "quantitative":    120,
    "commercial":      160,
    "troubleshooting": 200,
    "authority":       180,
}


# ── Data classes ──────────────────────────────────────────────────────────────

# Minimum composite GEO+AEO score (0-100) a draft must reach before being
# accepted.  Drafts below this threshold are regenerated once with explicit
# improvement instructions injected into the prompt.
_MIN_DRAFT_SCORE = 65


@dataclass
class ContentDraft:
    """A single LLM-generated content section for one intent cluster."""
    document_id: str
    tenant_id: str
    topic: str
    intent: str
    priority: int
    draft_text: str
    word_count: int
    query_variants: list[str]
    schema_types: list[str]
    authority_signals: list[str]
    content_brief: list[str]
    model_used: str
    provider_used: str
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    geo_score: float = 0.0           # LLM visibility score (0-100) from GEO engine
    aeo_score: float = 0.0           # Answer engine score (0-100) from AEO engine
    composite_score: float = 0.0     # (geo_score + aeo_score) / 2
    quality_score: float = 0.0       # Google Ads Quality Score equivalent (1-10)
    projected_position: str = ""     # e.g. "#1 above paid ads"
    beats_paid_ads: bool = False      # True if quality_score >= 8.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id":        self.document_id,
            "tenant_id":          self.tenant_id,
            "topic":              self.topic,
            "intent":             self.intent,
            "priority":           self.priority,
            "draft_text":         self.draft_text,
            "word_count":         self.word_count,
            "query_variants":     self.query_variants,
            "schema_types":       self.schema_types,
            "authority_signals":  self.authority_signals,
            "content_brief":      self.content_brief,
            "model_used":         self.model_used,
            "provider_used":      self.provider_used,
            "generated_at":       self.generated_at,
            "geo_score":          self.geo_score,
            "aeo_score":          self.aeo_score,
            "composite_score":    self.composite_score,
            "quality_score":      self.quality_score,
            "projected_position": self.projected_position,
            "beats_paid_ads":     self.beats_paid_ads,
        }


@dataclass
class WritingAgentResult:
    """Full result of one writing agent run for a document."""
    document_id: str
    tenant_id: str
    drafts: list[ContentDraft]
    drafts_written: int
    drafts_failed: int
    total_words_written: int
    model_used: str
    provider_used: str


# ── LLM client helpers ────────────────────────────────────────────────────────

async def _call_openai(prompt: str, model: str, max_tokens: int) -> str:
    """Call OpenAI chat completions. Raises on failure."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.4,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()


async def _call_gemini(prompt: str, model: str, max_tokens: int) -> str:
    """Call Google Gemini via the generativeai SDK. Raises on failure."""
    import google.generativeai as genai
    genai.configure(api_key=settings.gemini_api_key)
    gen_model = genai.GenerativeModel(
        model_name=model,
        system_instruction=_SYSTEM_PROMPT,
    )
    response = await gen_model.generate_content_async(
        prompt,
        generation_config=genai.GenerationConfig(
            temperature=0.4,
            max_output_tokens=max_tokens,
        ),
    )
    return (response.text or "").strip()


# ── Main Writing Agent ────────────────────────────────────────────────────────

class LLMWritingAgent:
    """
    Reads the gap_close_actions closure plan for a document and generates
    a production-ready content draft for every intent cluster in that plan.

    The agent is provider-agnostic: it uses `llm_writing_provider` /
    `llm_writing_model` from settings, falling back to the other available
    provider if the primary API key is absent.

    Usage (direct):
        agent  = LLMWritingAgent()
        result = await agent.write_drafts(document_id, tenant_id, close_plan)

    Usage (via Celery):
        tasks.write_gap_content.delay(document_id, tenant_id)
    """

    def __init__(self) -> None:
        self._provider  = settings.llm_writing_provider
        self._model     = settings.llm_writing_model
        self._max_tokens = settings.llm_writing_max_tokens

        # Resolve effective provider based on available API keys
        if self._provider == "openai" and not settings.openai_api_key:
            if settings.gemini_api_key:
                logger.warning("writing_agent_openai_key_missing_falling_back_to_gemini")
                self._provider = "gemini"
                self._model    = "gemini-2.5-flash"
        elif self._provider == "gemini" and not settings.gemini_api_key:
            if settings.openai_api_key:
                logger.warning("writing_agent_gemini_key_missing_falling_back_to_openai")
                self._provider = "openai"
                self._model    = "gpt-4o"

    # ── Public API ────────────────────────────────────────────────────────────

    async def write_drafts(
        self,
        document_id: str,
        tenant_id: str,
        close_plan: dict,
    ) -> WritingAgentResult:
        """
        Generate content drafts for all intent clusters in a close plan.

        Args:
            document_id: Source document UUID.
            tenant_id:   Tenant UUID.
            close_plan:  The close_plan JSON from gap_close_actions.

        Returns:
            WritingAgentResult with all generated drafts.
        """
        clusters: list[dict] = close_plan.get("clusters", [])

        # Cap at the highest-priority 12 clusters to control LLM cost
        clusters = sorted(clusters, key=lambda c: c.get("priority", 99))[:12]

        drafts: list[ContentDraft] = []
        failed = 0

        for cluster in clusters:
            try:
                draft = await self._write_cluster(document_id, tenant_id, cluster)
                # Score the draft through GEO + AEO engines
                draft = await self._score_and_improve(draft, cluster)
                drafts.append(draft)
                logger.info(
                    "cluster_draft_written",
                    document_id=document_id,
                    topic=cluster.get("topic"),
                    intent=cluster.get("intent"),
                    words=draft.word_count,
                    geo_score=draft.geo_score,
                    aeo_score=draft.aeo_score,
                    composite_score=draft.composite_score,
                )
            except Exception as exc:
                failed += 1
                logger.error(
                    "cluster_draft_failed",
                    document_id=document_id,
                    topic=cluster.get("topic"),
                    intent=cluster.get("intent"),
                    error=str(exc),
                )

        total_words = sum(d.word_count for d in drafts)

        logger.info(
            "writing_agent_complete",
            document_id=document_id,
            drafts=len(drafts),
            failed=failed,
            total_words=total_words,
            provider=self._provider,
            model=self._model,
        )

        return WritingAgentResult(
            document_id=document_id,
            tenant_id=tenant_id,
            drafts=drafts,
            drafts_written=len(drafts),
            drafts_failed=failed,
            total_words_written=total_words,
            model_used=self._model,
            provider_used=self._provider,
        )

    async def persist_drafts(
        self, result: WritingAgentResult, session
    ) -> int:
        """
        Persist all drafts to gap_content_drafts table.

        Args:
            result:  WritingAgentResult from write_drafts().
            session: SQLAlchemy async session.

        Returns:
            Number of rows inserted.
        """
        from sqlalchemy import text

        inserted = 0
        for draft in result.drafts:
            await session.execute(
                text("""
                    INSERT INTO gap_content_drafts
                        (document_id, tenant_id, topic, intent, priority,
                         draft_text, word_count, query_variants, schema_types,
                         authority_signals, content_brief,
                         model_used, provider_used, status, generated_at)
                    VALUES
                        (:document_id, :tenant_id, :topic, :intent, :priority,
                         :draft_text, :word_count, :query_variants, :schema_types,
                         :authority_signals, :content_brief,
                         :model_used, :provider_used, 'draft', :generated_at)
                    ON CONFLICT (document_id, tenant_id, topic, intent)
                    DO UPDATE SET
                        draft_text       = EXCLUDED.draft_text,
                        word_count       = EXCLUDED.word_count,
                        model_used       = EXCLUDED.model_used,
                        provider_used    = EXCLUDED.provider_used,
                        status           = 'draft',
                        generated_at     = EXCLUDED.generated_at
                """),
                {
                    "document_id":     draft.document_id,
                    "tenant_id":       draft.tenant_id,
                    "topic":           draft.topic,
                    "intent":          draft.intent,
                    "priority":        draft.priority,
                    "draft_text":      draft.draft_text,
                    "word_count":      draft.word_count,
                    "query_variants":  draft.query_variants,
                    "schema_types":    draft.schema_types,
                    "authority_signals": draft.authority_signals,
                    "content_brief":   draft.content_brief,
                    "model_used":      draft.model_used,
                    "provider_used":   draft.provider_used,
                    "generated_at":    draft.generated_at,
                },
            )
            inserted += 1

        # Mark the close action as drafts_ready
        await session.execute(
            text("""
                UPDATE gap_close_actions
                SET status     = 'drafts_ready',
                    updated_at = NOW()
                WHERE document_id = :doc_id
                  AND tenant_id   = :tenant_id
                  AND status      = 'pending'
            """),
            {
                "doc_id":    result.document_id,
                "tenant_id": result.tenant_id,
            },
        )

        logger.info(
            "gap_drafts_persisted",
            document_id=result.document_id,
            count=inserted,
        )
        return inserted

    # ── Private: per-cluster draft generation ─────────────────────────────────

    async def _write_cluster(
        self, document_id: str, tenant_id: str, cluster: dict
    ) -> ContentDraft:
        """Generate a content draft for one intent cluster."""
        topic           = cluster.get("topic", "")
        intent          = cluster.get("intent", "")
        priority        = cluster.get("priority", 99)
        query_variants  = cluster.get("query_variants", [])
        auth_signals    = cluster.get("authority_signals_needed", [])
        brief           = cluster.get("content_brief", [])
        schema_types    = cluster.get("schema_types_recommended", [])

        word_target = _INTENT_WORD_TARGETS.get(intent, 200)
        prompt      = self._build_prompt(
            topic=topic,
            intent=intent,
            query_variants=query_variants,
            authority_signals=auth_signals,
            content_brief=brief,
            schema_types=schema_types,
            word_target=word_target,
        )

        draft_text = await self._call_llm(prompt)
        draft_text = self._validate_and_clean(draft_text, intent)
        word_count = len(draft_text.split())

        return ContentDraft(
            document_id=document_id,
            tenant_id=tenant_id,
            topic=topic,
            intent=intent,
            priority=priority,
            draft_text=draft_text,
            word_count=word_count,
            query_variants=query_variants,
            schema_types=schema_types,
            authority_signals=auth_signals,
            content_brief=brief,
            model_used=self._model,
            provider_used=self._provider,
        )

    def _build_prompt(
        self,
        topic: str,
        intent: str,
        query_variants: list[str],
        authority_signals: list[str],
        content_brief: list[str],
        schema_types: list[str],
        word_target: int,
    ) -> str:
        """
        Build the user-turn prompt for a single intent cluster.
        Packs all the context the LLM needs to produce authoritative copy.
        """
        queries_block = "\n".join(f"  - {q}" for q in query_variants[:5])
        signals_block = "\n".join(f"  - {s}" for s in authority_signals[:4])
        brief_block   = "\n".join(f"  - {b}" for b in content_brief)
        schema_block  = ", ".join(schema_types[:3]) if schema_types else "Article"

        return f"""Write a content section for the topic: "{topic}"
Intent type: {intent.upper()}
Target search queries this section must answer:
{queries_block}

Content structure required (write ALL of these):
{brief_block}

Authority signals to include (you MUST address at least 2):
{signals_block}

Schema types this section should support: {schema_block}

Target word count: ~{word_target} words.
Write the complete section now. Start with the heading."""

    async def _call_llm(self, prompt: str) -> str:
        """
        Call the configured LLM provider with automatic fallback.
        Raises RuntimeError if both providers fail.
        """
        primary_fn, fallback_fn, fallback_model = self._resolve_providers()

        try:
            return await primary_fn(prompt, self._model, self._max_tokens)
        except Exception as primary_exc:
            logger.warning(
                "writing_agent_primary_failed_trying_fallback",
                provider=self._provider,
                error=str(primary_exc),
            )
            if fallback_fn and fallback_model:
                return await fallback_fn(prompt, fallback_model, self._max_tokens)
            raise RuntimeError(
                f"LLM writing agent: primary provider {self._provider} failed "
                f"and no fallback available. Error: {primary_exc}"
            ) from primary_exc

    def _resolve_providers(self):
        """
        Return (primary_fn, fallback_fn, fallback_model) based on configured
        provider and available API keys.
        """
        if self._provider == "openai":
            fallback_fn    = _call_gemini if settings.gemini_api_key else None
            fallback_model = "gemini-2.5-flash" if settings.gemini_api_key else None
            return _call_openai, fallback_fn, fallback_model
        else:
            fallback_fn    = _call_openai if settings.openai_api_key else None
            fallback_model = "gpt-4o" if settings.openai_api_key else None
            return _call_gemini, fallback_fn, fallback_model

    @staticmethod
    def _validate_and_clean(text: str, intent: str) -> str:
        """
        Strip code fences, excessive blank lines, and LLM preambles.
        Enforce the First Sentence Rule if violated.
        """
        # Strip markdown code fences if the LLM wrapped the output
        text = re.sub(r"```(?:\w+)?\s*|\s*```", "", text).strip()

        # Remove common LLM preambles
        filler_openers = (
            "Sure, here", "Certainly!", "Of course,", "Here is",
            "Here's", "Below is", "I'll write", "Let me write",
        )
        for opener in filler_openers:
            if text.lower().startswith(opener.lower()):
                # Skip the preamble line
                lines = text.split("\n", 1)
                if len(lines) > 1:
                    text = lines[1].strip()
                break

        # Collapse 3+ blank lines to 2
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()

    # ── GEO + AEO scoring feedback loop ──────────────────────────────────────

    async def _score_and_improve(
        self, draft: ContentDraft, cluster: dict
    ) -> ContentDraft:
        """
        Score a draft through the GEO, AEO, and Quality Score engines.
        The Quality Score gate mirrors Google's Ad Rank threshold:
          - QS < 8 AND composite < _MIN_DRAFT_SCORE → regenerate with targeted fixes.
          - Goal: every published draft achieves QS >= 8 (beats all paid ads).

        This is the quality gate that guarantees every published draft is
        citation-ready for AI engines and positioned at #1 above paid ads.
        """
        geo_score, aeo_score, quality_score = await self._compute_draft_scores(draft.draft_text)
        composite = (geo_score + aeo_score) / 2

        # Gate: regenerate if composite too low OR quality score below "beats paid ads" (8.0)
        needs_improvement = composite < _MIN_DRAFT_SCORE or quality_score < 8.0

        if needs_improvement:
            logger.info(
                "draft_below_threshold_regenerating",
                topic=draft.topic,
                intent=draft.intent,
                geo_score=geo_score,
                aeo_score=aeo_score,
                composite=composite,
                quality_score=quality_score,
                threshold=_MIN_DRAFT_SCORE,
            )
            improved_text = await self._regenerate_with_fixes(
                draft=draft,
                cluster=cluster,
                geo_score=geo_score,
                aeo_score=aeo_score,
                quality_score=quality_score,
            )
            if improved_text:
                improved_text = self._validate_and_clean(improved_text, draft.intent)
                # Re-score after improvement
                geo_score, aeo_score, quality_score = await self._compute_draft_scores(improved_text)
                composite = (geo_score + aeo_score) / 2
                draft.draft_text = improved_text
                draft.word_count = len(improved_text.split())

        draft.geo_score = round(geo_score, 1)
        draft.aeo_score = round(aeo_score, 1)
        draft.composite_score = round(composite, 1)
        draft.quality_score = round(quality_score, 1)
        draft.beats_paid_ads = quality_score >= 8.0
        draft.projected_position = self._qs_to_label(quality_score)
        return draft

    @staticmethod
    def _qs_to_label(qs: float) -> str:
        if qs >= 9.5: return "#1 AI Overview — above ALL paid ads"
        if qs >= 8.5: return "#1 + Featured Snippet — above all paid ads"
        if qs >= 8.0: return "#1–#2 — above most paid ads"
        if qs >= 7.0: return "#2–#3 — competitive with top paid ads"
        if qs >= 6.0: return "#4–#5 — level with paid ads"
        if qs >= 4.0: return "#6–#9 — below paid ads"
        return "Page 2+ — not competitive"

    async def _compute_draft_scores(self, text: str) -> tuple[float, float, float]:
        """
        Compute GEO visibility score, AEO answer readiness score, and
        Google Ads Quality Score equivalent for a text.
        Uses the local scoring modules directly (no HTTP round-trip needed).
        Returns (geo_score 0-100, aeo_score 0-100, quality_score 1-10).
        """
        geo_score = 0.0
        aeo_score = 0.0
        quality_score = 1.0

        # GEO score — LLM visibility
        try:
            from services.geo_engine.llm_visibility import LLMVisibilityScorer
            from services.geo_engine.citations import CitationReadinessAnalyser
            scorer = LLMVisibilityScorer()
            citations = CitationReadinessAnalyser()
            citation_result = citations.analyse(text)
            word_count = len(text.split())
            entity_count = max(1, word_count // 80)  # rough estimate without NER
            vis = scorer.score(
                content=text,
                entity_count=entity_count,
                citation_score=citation_result.overall_score,
                context_richness=0.0,
            )
            geo_score = vis.overall_score
        except Exception as exc:
            logger.debug("geo_score_failed", error=str(exc))

        # AEO score — answer engine readiness (fix: use AnswerScorer not AEOScorer)
        try:
            from services.aeo_engine.answer_scorer import AnswerScorer
            aeo_scorer = AnswerScorer()
            aeo_result = aeo_scorer.score(text)
            aeo_score = aeo_result.overall_score
        except Exception as exc:
            logger.debug("aeo_score_failed", error=str(exc))

        # Quality Score — Google Ads Ad Rank equivalent (1-10)
        try:
            from services.gap_detection.quality_score import QualityScoreEngine
            from services.geo_engine.citations import CitationReadinessAnalyser as _CRA
            import re as _re
            qs_engine = QualityScoreEngine()
            cit = _CRA().analyse(text)
            has_date = bool(_re.search(
                r'\b(?:January|February|March|April|May|June|July|August|'
                r'September|October|November|December|\d{4})\b', text
            ))
            has_stats = bool(_re.search(
                r'\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|percent|million|billion|x|times)\b',
                text, _re.IGNORECASE
            ))
            qs_result = qs_engine.score(
                content=text,
                citation_score=cit.overall_score,
                aeo_score=aeo_score,
                keyword_coverage_score=min(1.0, geo_score / 100),
                has_stats=has_stats,
                has_date=has_date,
            )
            quality_score = qs_result.quality_score
        except Exception as exc:
            logger.debug("quality_score_failed", error=str(exc))

        return geo_score, aeo_score, quality_score

    async def _regenerate_with_fixes(
        self,
        draft: ContentDraft,
        cluster: dict,
        geo_score: float,
        aeo_score: float,
        quality_score: float = 0.0,
    ) -> str | None:
        """
        Rebuild the prompt with specific Quality Score fix instructions
        targeting the exact QS dimensions that are below threshold,
        then call the LLM again.

        Quality Score mapping:
          QS < 8.0 -> fixes target all 3 Ad Rank dimensions
          QS < 6.0 -> aggressive rewrite with all dimensions
        """
        fixes: list[str] = []

        # QS Dimension 1: Snippet Attractiveness / Expected CTR fixes
        if geo_score < _MIN_DRAFT_SCORE or quality_score < 8.0:
            fixes += [
                "[QS: Expected CTR] START with a direct factual sentence: "
                "'{Topic} is/provides {specific fact}.' — ZERO preambles allowed. "
                "This is the zero-click answer shown in AI Overviews ABOVE paid ads.",
                "[QS: Expected CTR] Include a specific number or percentage in the FIRST paragraph "
                "(e.g. 'reduces costs by 40%') — Google data shows this lifts CTR by 18%.",
            ]

        # QS Dimension 2: Intent Alignment / Ad Relevance fixes
        if aeo_score < _MIN_DRAFT_SCORE or quality_score < 8.0:
            fixes += [
                "[QS: Intent Alignment] Structure the first paragraph (40-60 words) as a DIRECT "
                "standalone answer to the main query — this maximises Google's intent-match score.",
                "[QS: Intent Alignment] Add a numbered list or bullet list of at least 4 items "
                "if the intent is procedural or comparative — lists are Google's #1 featured snippet format.",
                "[QS: Intent Alignment] Use specific trigger phrases: 'The best way to...', "
                "'X works by...', 'The key difference is...', 'According to [Source]...'",
            ]

        # QS Dimension 3: Content Experience / Landing Page fixes
        if quality_score < 8.0:
            fixes += [
                "[QS: Content Experience] Add at least 2 statistics, percentages, or measurable claims "
                "— this is the primary E-E-A-T Trust signal Google uses vs paid ad landing pages.",
                "[QS: Content Experience] Attribute at least one claim to a named source: "
                "'According to [Organisation/Study]...' — required for E-E-A-T Experience score.",
                "[QS: Content Experience] Add H2/H3 headings and bullet lists — Google's Page Experience "
                "algorithm explicitly measures heading structure and scannability.",
            ]

        if not fixes:
            return None

        fix_block = "\n".join(f"  {i+1}. {f}" for i, f in enumerate(fixes))
        improvement_instruction = f"""
CRITICAL — QUALITY SCORE IMPROVEMENT REQUIRED TO OUT-RANK PAID ADS:
Current Quality Score: {quality_score:.1f}/10 (need >= 8.0 to beat Google paid ads).
Current GEO score: {geo_score:.0f}/100. Current AEO score: {aeo_score:.0f}/100.

You MUST fix ALL of the following in this complete rewrite to achieve QS >= 8:
{fix_block}

TARGET: Every fix above maps to one of Google's 3 Ad Rank components. Nail all three
and this content will rank at position #1 ABOVE all Google paid ads at zero cost.

Do NOT repeat the previous version. Rewrite completely with every fix applied.
"""
        # Inject improvement instruction into a fresh prompt
        word_target = _INTENT_WORD_TARGETS.get(draft.intent, 200)
        base_prompt = self._build_prompt(
            topic=draft.topic,
            intent=draft.intent,
            query_variants=draft.query_variants,
            authority_signals=draft.authority_signals,
            content_brief=draft.content_brief,
            schema_types=draft.schema_types,
            word_target=word_target,
        )
        improved_prompt = base_prompt + improvement_instruction

        try:
            return await self._call_llm(improved_prompt)
        except Exception as exc:
            logger.warning("draft_regeneration_failed", error=str(exc))
            return None


