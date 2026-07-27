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

_SYSTEM_PROMPT = """You are a world-class SEO, GEO, and AEO content writer.
Your sole job is to write a production-ready content SECTION that will:
  1. Rank on the FIRST PAGE of Google and Bing for the target queries.
  2. Be cited FIRST by AI engines (ChatGPT, Perplexity, Google AI Overviews).
  3. Satisfy the FIRST SENTENCE RULE: open with a direct, factual answer —
     "{Subject} is/provides/does {specific fact}." — no fluff openers.
  4. Include at least one statistic, percentage, or measurable claim.
  5. Use active voice throughout.
  6. Be structured so an AI can excerpt the answer without reading the full page.

Rules you must NEVER break:
  - Do NOT start with "In this section", "Welcome", "This article", or "Today".
  - Every paragraph must contain a standalone citable fact.
  - If the intent is PROCEDURAL: number every step (1. 2. 3.).
  - If the intent is COMPARATIVE: include a markdown comparison table.
  - If the intent is QUANTITATIVE: lead with the most impactful number.
  - If the intent is DEFINITIONAL: first sentence must be a subject-first definition.
  - If the intent is FAQ/TROUBLESHOOTING: structure as Q: / A: pairs.

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id":      self.document_id,
            "tenant_id":        self.tenant_id,
            "topic":            self.topic,
            "intent":           self.intent,
            "priority":         self.priority,
            "draft_text":       self.draft_text,
            "word_count":       self.word_count,
            "query_variants":   self.query_variants,
            "schema_types":     self.schema_types,
            "authority_signals": self.authority_signals,
            "content_brief":    self.content_brief,
            "model_used":       self.model_used,
            "provider_used":    self.provider_used,
            "generated_at":     self.generated_at,
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
                self._model    = "gemini-1.5-pro"
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
                drafts.append(draft)
                logger.info(
                    "cluster_draft_written",
                    document_id=document_id,
                    topic=cluster.get("topic"),
                    intent=cluster.get("intent"),
                    words=draft.word_count,
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
            fallback_model = "gemini-1.5-pro" if settings.gemini_api_key else None
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
