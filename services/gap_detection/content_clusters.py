"""
DATA ENGINE — Intent-Based Content Clusters (GEO Pillar 4)
Maps content gaps to deep, multi-layered query clusters for AI search authority.

Strategy (GEO — Generative Engine Optimisation):
  Traditional SEO targets single keywords. AI search (Perplexity, ChatGPT,
  Google AI Overviews) processes multi-part, nuanced queries where the
  best answer is the one that most completely covers all facets of a topic.

  Intent-Based Content Clusters build a hierarchy of query intent around
  every missing topic detected by the GapAnalyzer:

    Topic: "AI inventory management"
    └─ Informational cluster
       ├─ "What is AI inventory management?"          (definitional)
       ├─ "How does AI predict stock levels?"         (procedural)
       ├─ "Why do businesses use AI for inventory?"   (causal)
       └─ "Which AI tools manage inventory?"          (comparative)
    └─ Transactional cluster
       ├─ "Best AI inventory software for Shopify"   (commercial)
       └─ "AI inventory management pricing 2024"     (commercial)
    └─ Data/Authority cluster
       ├─ Statistics about AI in inventory
       └─ Case studies: measurable outcomes

  Each cluster produces:
    - A ranked list of query variants to address in content
    - A depth score (0–100) measuring how completely the doc covers the cluster
    - Authority signals: stats needed, entities to cite, schema types to add
    - A content brief: the minimum sections required to satisfy the cluster

  This directly feeds the GEO ranking signal for "comprehensive topic coverage"
  which AI engines use to select the most authoritative citation source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Intent types ──────────────────────────────────────────────────────────────

class QueryIntent:
    DEFINITIONAL   = "definitional"    # What is X?
    PROCEDURAL     = "procedural"      # How do I / How does X work?
    CAUSAL         = "causal"          # Why does / Why is X important?
    COMPARATIVE    = "comparative"     # X vs Y / Best X for Y
    QUANTITATIVE   = "quantitative"    # How many / How much / What % ?
    COMMERCIAL     = "commercial"      # Buy X / X pricing / X for [platform]
    TROUBLESHOOTING = "troubleshooting" # X not working / Fix X / X error
    AUTHORITY      = "authority"       # Statistics / Research / Case studies


INTENT_QUESTION_TEMPLATES: dict[str, list[str]] = {
    QueryIntent.DEFINITIONAL: [
        "What is {topic}?",
        "What does {topic} mean?",
        "Define {topic}",
        "{topic} explained",
        "What are {topic} basics?",
    ],
    QueryIntent.PROCEDURAL: [
        "How does {topic} work?",
        "How to use {topic}",
        "How to implement {topic}",
        "Steps to {topic}",
        "Getting started with {topic}",
        "How to set up {topic}",
    ],
    QueryIntent.CAUSAL: [
        "Why is {topic} important?",
        "Why use {topic}?",
        "Benefits of {topic}",
        "Advantages of {topic}",
        "How {topic} helps businesses",
        "Impact of {topic}",
    ],
    QueryIntent.COMPARATIVE: [
        "{topic} vs alternatives",
        "Best {topic} tools",
        "{topic} comparison",
        "Top {topic} solutions",
        "{topic} for enterprise vs small business",
    ],
    QueryIntent.QUANTITATIVE: [
        "{topic} statistics",
        "{topic} data and trends",
        "How much does {topic} cost?",
        "{topic} ROI",
        "{topic} market size",
        "{topic} growth rate",
    ],
    QueryIntent.COMMERCIAL: [
        "Best {topic} software",
        "{topic} pricing",
        "{topic} for Shopify",
        "{topic} for ecommerce",
        "{topic} enterprise solution",
        "Buy {topic}",
    ],
    QueryIntent.TROUBLESHOOTING: [
        "{topic} problems",
        "{topic} common issues",
        "{topic} not working",
        "Fix {topic} errors",
        "{topic} troubleshooting guide",
    ],
    QueryIntent.AUTHORITY: [
        "{topic} research",
        "{topic} case studies",
        "{topic} examples",
        "{topic} best practices",
        "{topic} industry report",
        "{topic} white paper",
    ],
}

# Coverage markers: words/patterns that indicate a specific intent is addressed
INTENT_COVERAGE_MARKERS: dict[str, list[str]] = {
    QueryIntent.DEFINITIONAL:   [r'\b(?:is|are|refers to|defined as|means)\b'],
    QueryIntent.PROCEDURAL:     [r'\b(?:step \d|how to|process|workflow|implement)\b'],
    QueryIntent.CAUSAL:         [r'\b(?:because|therefore|benefit|advantage|result|impact)\b'],
    QueryIntent.COMPARATIVE:    [r'\b(?:vs\.?|versus|compared to|better than|unlike|whereas)\b'],
    QueryIntent.QUANTITATIVE:   [r'\b\d+(?:\.\d+)?(?:%|x|times|billion|million|thousand)\b'],
    QueryIntent.COMMERCIAL:     [r'\b(?:price|pricing|cost|subscription|plan|tier|free trial)\b'],
    QueryIntent.TROUBLESHOOTING:[r'\b(?:error|issue|problem|fix|resolve|troubleshoot|debug)\b'],
    QueryIntent.AUTHORITY:      [r'\b(?:study|research|report|according to|data shows|found that)\b'],
}


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class IntentCluster:
    """
    A group of semantically related queries sharing the same intent type
    around a single topic. Each cluster maps to a content section
    that must exist for the document to rank as authoritative on that topic.
    """
    topic: str
    intent: str                             # QueryIntent constant
    query_variants: list[str]               # specific questions to address
    coverage_score: float                   # 0–100: how well the doc covers this
    depth_score: float                      # 0–100: depth of existing treatment
    authority_signals_needed: list[str]     # stats, entities, schema types
    content_brief: list[str]                # minimum sections to write
    schema_types_recommended: list[str]     # FAQPage, HowTo, Dataset, etc.
    priority: int                           # 1 = highest


@dataclass
class ContentClusterReport:
    """
    Full Intent-Based Content Cluster analysis for a document.
    Maps every missing topic to a set of intent clusters and produces
    a content authority score and actionable content brief.
    """
    document_id: str
    tenant_id: str
    total_topics: int
    clusters: list[IntentCluster]
    authority_score: float              # 0–100 composite authority score
    coverage_gaps: list[str]            # intents with zero coverage
    top_priority_clusters: list[str]    # top 5 most impactful clusters
    full_content_brief: list[str]       # complete ordered list of sections to write
    estimated_words_needed: int         # rough estimate of content to add


# ── Main Cluster Builder ──────────────────────────────────────────────────────

class ContentClusterBuilder:
    """
    Builds Intent-Based Content Clusters from gap analysis missing topics.

    For each missing topic the GapAnalyzer identified, this builder:
      1. Generates query variants for all 8 intent types
      2. Scores existing document coverage per intent
      3. Produces a content brief (sections to write) per cluster
      4. Assigns schema type recommendations
      5. Computes an aggregate authority score

    The output tells content writers EXACTLY what to write and in what order
    to maximise AI search authority — not just "write about X" but
    "write a 200-word definitional answer, a 5-step procedural section,
    3 comparison rows, and 2 quantitative statistics for topic X."
    """

    # Words to add per intent section (used for estimating content volume)
    WORDS_PER_INTENT: dict[str, int] = {
        QueryIntent.DEFINITIONAL:    150,
        QueryIntent.PROCEDURAL:      300,
        QueryIntent.CAUSAL:          200,
        QueryIntent.COMPARATIVE:     250,
        QueryIntent.QUANTITATIVE:    100,
        QueryIntent.COMMERCIAL:      150,
        QueryIntent.TROUBLESHOOTING: 200,
        QueryIntent.AUTHORITY:       150,
    }

    def build(
        self,
        document_id: str,
        tenant_id: str,
        missing_topics: list[str],
        document_content: str = "",
        max_clusters_per_topic: int = 4,
    ) -> ContentClusterReport:
        """
        Build a full content cluster report from missing topics.

        Args:
            document_id:            Source document UUID.
            tenant_id:              Tenant UUID.
            missing_topics:         Topics from GapAnalyzer.missing_topics.
            document_content:       Full document text for coverage scoring.
            max_clusters_per_topic: Limit clusters per topic to avoid noise.

        Returns:
            ContentClusterReport with full intent map and content brief.
        """
        all_clusters: list[IntentCluster] = []
        priority = 1

        for topic in missing_topics[:15]:  # cap at 15 topics for performance
            topic_clusters = self._build_topic_clusters(
                topic=topic,
                content=document_content,
                max_clusters=max_clusters_per_topic,
                base_priority=priority,
            )
            all_clusters.extend(topic_clusters)
            priority += len(topic_clusters)

        # Sort by coverage gap (lowest coverage = highest priority)
        all_clusters.sort(key=lambda c: (c.coverage_score, c.depth_score))
        for i, cluster in enumerate(all_clusters):
            cluster.priority = i + 1

        authority_score = self._compute_authority_score(all_clusters)
        coverage_gaps = self._identify_coverage_gaps(all_clusters)
        top_5 = self._select_top_priority_clusters(all_clusters)
        brief = self._build_full_content_brief(all_clusters[:20])
        estimated_words = self._estimate_words_needed(all_clusters)

        report = ContentClusterReport(
            document_id=document_id,
            tenant_id=tenant_id,
            total_topics=len(missing_topics),
            clusters=all_clusters,
            authority_score=round(authority_score, 1),
            coverage_gaps=coverage_gaps,
            top_priority_clusters=top_5,
            full_content_brief=brief,
            estimated_words_needed=estimated_words,
        )

        logger.info(
            "content_clusters_built",
            document_id=document_id,
            topics=len(missing_topics),
            clusters=len(all_clusters),
            authority_score=round(authority_score, 1),
            words_needed=estimated_words,
        )
        return report

    # ── Private: cluster generation ────────────────────────────────────────

    def _build_topic_clusters(
        self,
        topic: str,
        content: str,
        max_clusters: int,
        base_priority: int,
    ) -> list[IntentCluster]:
        """
        Generate one IntentCluster per intent type for a topic,
        selecting the most impactful `max_clusters` by coverage gap.
        """
        clusters: list[IntentCluster] = []

        # Priority intent order: definitional first (AI engines expect clear definitions)
        intent_order = [
            QueryIntent.DEFINITIONAL,
            QueryIntent.PROCEDURAL,
            QueryIntent.CAUSAL,
            QueryIntent.QUANTITATIVE,
            QueryIntent.COMPARATIVE,
            QueryIntent.AUTHORITY,
            QueryIntent.COMMERCIAL,
            QueryIntent.TROUBLESHOOTING,
        ]

        for intent in intent_order:
            coverage = self._score_intent_coverage(topic, intent, content)
            depth = self._score_depth(topic, intent, content)
            queries = self._generate_queries(topic, intent)
            auth_signals = self._authority_signals(topic, intent)
            brief_items = self._content_brief_items(topic, intent)
            schema_types = self._recommend_schema_types(intent)

            clusters.append(IntentCluster(
                topic=topic,
                intent=intent,
                query_variants=queries,
                coverage_score=round(coverage, 1),
                depth_score=round(depth, 1),
                authority_signals_needed=auth_signals,
                content_brief=brief_items,
                schema_types_recommended=schema_types,
                priority=base_priority,
            ))

        # Return only the clusters with the lowest coverage (highest need)
        clusters.sort(key=lambda c: c.coverage_score)
        return clusters[:max_clusters]

    # ── Private: scoring ───────────────────────────────────────────────────

    @staticmethod
    def _score_intent_coverage(topic: str, intent: str, content: str) -> float:
        """
        Score how well the existing document covers a specific intent for a topic.
        Returns 0–100 (0 = not covered at all, 100 = fully covered).
        """
        if not content:
            return 0.0

        # Check if topic appears in content at all
        if topic.lower() not in content.lower():
            return 0.0

        # Check for intent-specific markers near the topic
        markers = INTENT_COVERAGE_MARKERS.get(intent, [])
        content_lower = content.lower()

        # Find topic context window (±200 chars)
        idx = content_lower.find(topic.lower())
        if idx < 0:
            return 0.0

        context = content_lower[max(0, idx - 200): idx + 400]
        matches = 0
        for marker_pattern in markers:
            if re.search(marker_pattern, context, re.IGNORECASE):
                matches += 1

        if not markers:
            return 30.0  # topic present but no markers to check

        # Scale: 1 marker = 30, 2 = 60, 3+ = 90
        base = min(90.0, matches * 30.0)

        # Bonus: multiple occurrences of topic in content = depth
        occurrences = len(re.findall(re.escape(topic.lower()), content_lower))
        depth_bonus = min(10.0, occurrences * 2.0)

        return round(min(100.0, base + depth_bonus), 1)

    @staticmethod
    def _score_depth(topic: str, intent: str, content: str) -> float:
        """
        Score the depth of treatment for a topic+intent combination.
        Depth = word count of surrounding paragraphs / expected word count.
        """
        if not content or topic.lower() not in content.lower():
            return 0.0

        expected = ContentClusterBuilder.WORDS_PER_INTENT.get(intent, 150)

        # Extract paragraphs mentioning the topic
        paragraphs = [
            p for p in content.split("\n\n")
            if topic.lower() in p.lower()
        ]
        topic_words = sum(len(p.split()) for p in paragraphs)

        return round(min(100.0, (topic_words / expected) * 100), 1)

    # ── Private: generation ────────────────────────────────────────────────

    @staticmethod
    def _generate_queries(topic: str, intent: str) -> list[str]:
        templates = INTENT_QUESTION_TEMPLATES.get(intent, [])
        return [t.replace("{topic}", topic) for t in templates[:5]]

    @staticmethod
    def _authority_signals(topic: str, intent: str) -> list[str]:
        """
        Return the evidence / authority signals needed for this intent cluster
        to satisfy AI citation standards.
        """
        base: list[str] = []
        if intent == QueryIntent.QUANTITATIVE:
            base = [
                f"Include at least 2 statistics about {topic} (with source attribution).",
                f"Add a percentage or growth rate figure for {topic}.",
                f"Reference a named industry report or study on {topic}.",
            ]
        elif intent == QueryIntent.DEFINITIONAL:
            base = [
                f"Start the definition of {topic} with a subject-first sentence.",
                f"Include the Schema.org type that maps to {topic}.",
                f"Link {topic} to a Wikidata or Wikipedia entry via sameAs.",
            ]
        elif intent == QueryIntent.COMPARATIVE:
            base = [
                f"Create a comparison table: {topic} vs 2–3 alternatives.",
                f"State at least one measurable differentiator for {topic}.",
            ]
        elif intent == QueryIntent.AUTHORITY:
            base = [
                f"Cite a named research study or analyst report on {topic}.",
                "Include a real-world case study with measurable outcomes.",
                "Reference a recognised authority (Gartner, McKinsey, etc.).",
            ]
        elif intent == QueryIntent.PROCEDURAL:
            base = [
                f"Break {topic} into 3–7 numbered steps.",
                "Add a HowTo Schema.org block for the steps.",
                "State the expected outcome after following the steps.",
            ]
        else:
            base = [
                f"Provide at least one concrete example of {topic}.",
                f"Use a named entity (company, person, or product) when discussing {topic}.",
            ]
        return base

    @staticmethod
    def _content_brief_items(topic: str, intent: str) -> list[str]:
        """
        Minimum content sections to write for this cluster to satisfy AI search.
        """
        briefs: dict[str, list[str]] = {
            QueryIntent.DEFINITIONAL: [
                f"H2: What is {topic}? (150 words, subject-first definition)",
                f"One-sentence summary of {topic} for AI snippet extraction.",
            ],
            QueryIntent.PROCEDURAL: [
                f"H2: How {topic} Works (or: How to Use {topic})",
                "Numbered list of 3–7 concrete steps (HowTo schema)",
                "Expected outcome/result after completing the steps",
            ],
            QueryIntent.CAUSAL: [
                f"H3: Why {topic} Matters (or: Benefits of {topic})",
                "Bullet list of 3–5 specific benefits with measurable outcomes",
            ],
            QueryIntent.COMPARATIVE: [
                f"H2: {topic} vs Alternatives",
                "Comparison table with 3+ rows and clear differentiators",
            ],
            QueryIntent.QUANTITATIVE: [
                f"H3: {topic} by the Numbers",
                "2–3 statistics with source citations",
                "Optional: chart or table with data",
            ],
            QueryIntent.COMMERCIAL: [
                f"H3: {topic} Pricing / Plans",
                "Offer schema with price and availability",
                "Call to action with URL",
            ],
            QueryIntent.TROUBLESHOOTING: [
                f"H2: Common {topic} Issues",
                "FAQ block: 3 Q&A pairs for common problems (FAQPage schema)",
            ],
            QueryIntent.AUTHORITY: [
                f"H3: {topic} Research and Case Studies",
                "1 named case study with before/after metrics",
                "1 citation from recognised industry source",
            ],
        }
        return briefs.get(intent, [f"Add a section covering {topic} ({intent} intent)"])

    @staticmethod
    def _recommend_schema_types(intent: str) -> list[str]:
        mapping: dict[str, list[str]] = {
            QueryIntent.DEFINITIONAL:    ["Article", "WebPage"],
            QueryIntent.PROCEDURAL:      ["HowTo", "Article"],
            QueryIntent.CAUSAL:          ["Article", "FAQPage"],
            QueryIntent.COMPARATIVE:     ["Article", "Dataset"],
            QueryIntent.QUANTITATIVE:    ["Dataset", "Article"],
            QueryIntent.COMMERCIAL:      ["Product", "Offer"],
            QueryIntent.TROUBLESHOOTING: ["FAQPage", "HowTo"],
            QueryIntent.AUTHORITY:       ["Article", "Dataset"],
        }
        return mapping.get(intent, ["Article"])

    # ── Private: report aggregation ────────────────────────────────────────

    @staticmethod
    def _compute_authority_score(clusters: list[IntentCluster]) -> float:
        """
        Composite authority score (0–100).
        100 = all intent clusters for all topics have coverage ≥ 70%.
        """
        if not clusters:
            return 0.0
        avg_coverage = sum(c.coverage_score for c in clusters) / len(clusters)
        # Bonus for having clusters across ≥ 4 distinct intent types
        distinct_intents = len({c.intent for c in clusters})
        diversity_bonus = min(10.0, (distinct_intents - 1) * 2.0)
        return min(100.0, avg_coverage + diversity_bonus)

    @staticmethod
    def _identify_coverage_gaps(clusters: list[IntentCluster]) -> list[str]:
        """Return intent types where NO cluster has coverage > 0."""
        covered_intents = {c.intent for c in clusters if c.coverage_score > 0}
        all_intents = set(INTENT_QUESTION_TEMPLATES.keys())
        gaps = all_intents - covered_intents
        return sorted(gaps)

    @staticmethod
    def _select_top_priority_clusters(clusters: list[IntentCluster]) -> list[str]:
        """
        Select the top 5 clusters by impact (lowest coverage, high-value intent).
        Returns human-readable action strings.
        """
        # Weight intents by AI citation value
        intent_weights: dict[str, float] = {
            QueryIntent.DEFINITIONAL:  1.0,
            QueryIntent.QUANTITATIVE:  0.9,
            QueryIntent.PROCEDURAL:    0.85,
            QueryIntent.CAUSAL:        0.8,
            QueryIntent.AUTHORITY:     0.75,
            QueryIntent.COMPARATIVE:   0.7,
            QueryIntent.COMMERCIAL:    0.65,
            QueryIntent.TROUBLESHOOTING: 0.6,
        }

        scored = [
            (
                (100.0 - c.coverage_score) * intent_weights.get(c.intent, 0.5),
                c,
            )
            for c in clusters
            if c.coverage_score < 80.0  # only suggest genuinely missing content
        ]
        scored.sort(key=lambda x: x[0], reverse=True)

        actions: list[str] = []
        for _, cluster in scored[:5]:
            first_query = cluster.query_variants[0] if cluster.query_variants else cluster.topic
            actions.append(
                f"[{cluster.intent.upper()}] {cluster.topic}: "
                f"Address '{first_query}' "
                f"(coverage {cluster.coverage_score:.0f}% → target 80%)"
            )
        return actions

    @staticmethod
    def _build_full_content_brief(clusters: list[IntentCluster]) -> list[str]:
        """Flatten all content brief items across clusters into a single ordered list."""
        seen: set[str] = set()
        brief: list[str] = []
        for cluster in clusters:
            for item in cluster.content_brief:
                if item not in seen:
                    seen.add(item)
                    brief.append(item)
        return brief

    @staticmethod
    def _estimate_words_needed(clusters: list[IntentCluster]) -> int:
        """Estimate total words to write to fill all coverage gaps."""
        total = 0
        for cluster in clusters:
            gap_fraction = (100.0 - cluster.coverage_score) / 100.0
            per_intent = ContentClusterBuilder.WORDS_PER_INTENT.get(cluster.intent, 150)
            total += int(gap_fraction * per_intent)
        return total

    def to_dict(self, report: ContentClusterReport) -> dict[str, Any]:
        """Serialise a ContentClusterReport to a JSON-compatible dict."""
        return {
            "document_id":           report.document_id,
            "tenant_id":             report.tenant_id,
            "total_topics":          report.total_topics,
            "authority_score":       report.authority_score,
            "coverage_gaps":         report.coverage_gaps,
            "top_priority_clusters": report.top_priority_clusters,
            "full_content_brief":    report.full_content_brief,
            "estimated_words_needed": report.estimated_words_needed,
            "clusters": [
                {
                    "topic":                      c.topic,
                    "intent":                     c.intent,
                    "priority":                   c.priority,
                    "coverage_score":             c.coverage_score,
                    "depth_score":                c.depth_score,
                    "query_variants":             c.query_variants,
                    "authority_signals_needed":   c.authority_signals_needed,
                    "content_brief":              c.content_brief,
                    "schema_types_recommended":   c.schema_types_recommended,
                }
                for c in report.clusters
            ],
        }
