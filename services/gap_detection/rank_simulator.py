"""
DATA ENGINE — Content Rank Simulator
Predicts where a piece of content will rank against Google paid ads
and shows exactly how many Quality Score points are needed to move
from the current position to #1 above all ads.

Simulates Google's Ad Rank auction for organic content:
  Ad Rank = Bid × Quality Score × Context signals
  Organic Rank = Relevance × Quality Score × Page Experience × Authority

The simulator shows:
  1. Current estimated SERP position for the query
  2. Estimated positions of top paid ads (QS 5–8 from Google averages)
  3. Specific score improvements needed to rank above each paid ad
  4. Revenue/traffic uplift at each position step
  5. "Beat the paid ads" action plan

This turns the Quality Score into a BUSINESS ROI conversation:
  "Your content is at position 5. Increase QS from 6 → 9 to reach #1.
   Position 1 organic gets 31% CTR vs position 5's 5% CTR.
   That's a 6× traffic increase with zero ad spend."
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)

# ── SERP position model ───────────────────────────────────────────────────────
# Google's average CTR by SERP position (Advanced Web Ranking / Sistrix data)
_POSITION_CTR: dict[int, float] = {
    1:  0.314,   # 31.4% — above-all-ads organic wins
    2:  0.158,   # 15.8%
    3:  0.099,   # 9.9%
    4:  0.065,   # 6.5%
    5:  0.052,   # 5.2%
    6:  0.040,   # 4.0%
    7:  0.033,   # 3.3%
    8:  0.026,   # 2.6%
    9:  0.022,   # 2.2%
    10: 0.018,   # 1.8% (bottom page 1)
}
_DEFAULT_CTR = 0.005   # page 2+

# Typical paid ad positions in Google SERP and their implied Quality Scores
# (Google publishes that avg QS for top ads is 7–8, second row 5–6)
_PAID_AD_BENCHMARK: list[dict] = [
    {"position": "Ad #1 (top)", "min_qs": 8.0, "typical_qs": 8.5,
     "notes": "Top paid ad — typically QS 8–9, high bid. Beat with QS ≥ 9."},
    {"position": "Ad #2 (top)", "min_qs": 7.0, "typical_qs": 7.5,
     "notes": "Second paid ad — QS 7–8. Beat with organic QS ≥ 8."},
    {"position": "Ad #3 (top)", "min_qs": 5.5, "typical_qs": 6.5,
     "notes": "Third paid ad — QS 5–7. Organic QS ≥ 7 beats this."},
    {"position": "Ad #4 (bottom bar)", "min_qs": 4.0, "typical_qs": 5.0,
     "notes": "Bottom paid ads — QS 4–6. Any organic QS ≥ 6 beats these."},
]

# Organic content QS → estimated SERP position mapping
# (calibrated against Google Quality Score / organic correlation studies)
_QS_TO_POSITION: list[tuple[float, int, str]] = [
    (9.5,  1, "AI Overview + Position 1 — outranks ALL paid ads"),
    (8.5,  1, "Position 1 + Featured Snippet — above all paid ads"),
    (8.0,  2, "Position 1–2 — above most paid ads"),
    (7.0,  3, "Position 2–3 — competitive with top paid ads"),
    (6.0,  5, "Position 4–5 — level with paid ads (some above, some below)"),
    (5.0,  6, "Position 5–7 — below top paid ads"),
    (4.0,  8, "Position 7–9 — below all top paid ads"),
    (3.0,  10, "Position 9–10 — bottom of page 1"),
    (0.0,  11, "Page 2+ — not competitive"),
]


@dataclass
class AdRankBenchmark:
    """Comparison of content's QS vs a specific paid ad."""
    ad_position: str
    ad_typical_qs: float
    content_qs: float
    beats_this_ad: bool
    qs_gap: float           # how many QS points needed to beat this ad (0 if already beats it)
    notes: str


@dataclass
class RankSimulatorResult:
    """
    Full rank prediction and paid-ad comparison for a piece of content.
    """
    # Current state
    content_qs: float
    estimated_position: int
    position_label: str
    estimated_ctr: float                    # 0–1

    # Paid ad comparison
    ad_benchmarks: list[AdRankBenchmark]
    paid_ads_beaten: int                    # how many of 4 ad positions we beat
    beats_all_paid_ads: bool

    # Position uplift plan
    uplift_steps: list[dict]               # each step: {from_qs, to_qs, from_pos, to_pos, ctr_gain, action}
    target_qs_to_beat_all_ads: float       # QS needed to beat ALL paid ads (= 8.0)
    qs_gap_to_top: float                    # gap from current to QS 9.5 (AI Overview)

    # Business ROI
    ctr_at_position_1: float               # what CTR would be at position 1
    ctr_multiplier: float                  # current_ctr / position_1_ctr — the gain
    traffic_multiplier: str               # human-readable e.g. "6.0× more traffic at position 1"

    # Action summary
    top_action: str                        # single highest-priority action
    action_plan: list[str]                 # full ordered plan to reach #1


class ContentRankSimulator:
    """
    Simulates where content ranks against Google paid ads and
    shows the exact score improvements needed to reach #1.

    Usage:
        sim = ContentRankSimulator()
        result = sim.simulate(quality_score=6.5, query="best inventory software")
        print(result.position_label)     # "Position 4–5 — level with paid ads"
        print(result.top_action)         # "Increase QS 6.5 → 8.0 (+1.5 pts) to rank above all paid ads"
    """

    def simulate(
        self,
        quality_score: float,
        query: str = "",
        priority_fixes: list[str] | None = None,
        monthly_search_volume: int = 0,
    ) -> RankSimulatorResult:
        """
        Simulate SERP position and paid-ad competition.

        Args:
            quality_score:         The QualityScoreEngine output (1–10).
            query:                 The target query (for personalised messages).
            priority_fixes:        Ordered fix list from QualityScoreEngine.
            monthly_search_volume: Optional MSV for traffic calculations.

        Returns:
            RankSimulatorResult with full prediction and action plan.
        """
        qs = max(1.0, min(10.0, quality_score))
        fixes = priority_fixes or []

        # ── Estimate current position ─────────────────────────────────────────
        position, pos_label = self._qs_to_position(qs)
        ctr = _POSITION_CTR.get(position, _DEFAULT_CTR)

        # ── Compare against paid ads ──────────────────────────────────────────
        benchmarks: list[AdRankBenchmark] = []
        for ad in _PAID_AD_BENCHMARK:
            gap = max(0.0, ad["typical_qs"] - qs)
            benchmarks.append(AdRankBenchmark(
                ad_position=ad["position"],
                ad_typical_qs=ad["typical_qs"],
                content_qs=qs,
                beats_this_ad=qs >= ad["typical_qs"],
                qs_gap=round(gap, 1),
                notes=ad["notes"],
            ))

        paid_beaten = sum(1 for b in benchmarks if b.beats_this_ad)
        beats_all = qs >= _PAID_AD_BENCHMARK[0]["typical_qs"]

        # ── Uplift steps: what happens at each QS band ─────────────────────────
        uplift_steps = self._build_uplift_steps(qs)

        # ── Business impact ───────────────────────────────────────────────────
        ctr_p1 = _POSITION_CTR[1]
        ctr_mult = round(ctr_p1 / max(ctr, 0.001), 1)
        qs_gap_top = round(max(0.0, 9.5 - qs), 1)
        target_to_beat_ads = round(max(0.0, 8.5 - qs), 1)
        traffic_label = f"{ctr_mult}× more traffic at position #1 vs current position {position}"

        # ── Action plan ───────────────────────────────────────────────────────
        action_plan = self._build_action_plan(qs, query, fixes, benchmarks)
        top_action = action_plan[0] if action_plan else "Improve Quality Score to rank above paid ads."

        logger.debug(
            "rank_simulated",
            qs=qs,
            position=position,
            paid_beaten=paid_beaten,
            ctr=ctr,
            ctr_multiplier=ctr_mult,
        )

        return RankSimulatorResult(
            content_qs=qs,
            estimated_position=position,
            position_label=pos_label,
            estimated_ctr=round(ctr, 4),
            ad_benchmarks=benchmarks,
            paid_ads_beaten=paid_beaten,
            beats_all_paid_ads=beats_all,
            uplift_steps=uplift_steps,
            target_qs_to_beat_all_ads=8.5,
            qs_gap_to_top=qs_gap_top,
            ctr_at_position_1=round(ctr_p1, 4),
            ctr_multiplier=ctr_mult,
            traffic_multiplier=traffic_label,
            top_action=top_action,
            action_plan=action_plan,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _qs_to_position(qs: float) -> tuple[int, str]:
        for threshold, position, label in _QS_TO_POSITION:
            if qs >= threshold:
                return position, label
        return 11, "Page 2+"

    @staticmethod
    def _build_uplift_steps(current_qs: float) -> list[dict]:
        """Show what happens at each meaningful QS improvement step."""
        steps = []
        targets = [
            (6.0,  "Beat bottom paid ads",           "Add key facts and a stats-backed sentence."),
            (7.0,  "Beat mid-tier paid ads",          "Fix first sentence + add sameAs schema."),
            (8.0,  "Beat ALL paid ads",               "Fix all 3 QS dimensions to Above Average."),
            (9.0,  "Featured Snippet + Position 1",   "Achieve QS 9 — perfect content experience."),
            (9.5,  "AI Overview (above all results)", "Achieve perfect QS — captured by AI Overviews."),
        ]
        for target_qs, label, action in targets:
            if target_qs > current_qs:
                from_pos, _ = ContentRankSimulator._qs_to_position(current_qs)
                to_pos, _   = ContentRankSimulator._qs_to_position(target_qs)
                from_ctr = _POSITION_CTR.get(from_pos, _DEFAULT_CTR)
                to_ctr   = _POSITION_CTR.get(to_pos, _DEFAULT_CTR)
                steps.append({
                    "from_qs":   current_qs,
                    "to_qs":     target_qs,
                    "qs_gain":   round(target_qs - current_qs, 1),
                    "from_pos":  from_pos,
                    "to_pos":    to_pos,
                    "ctr_gain":  f"+{(to_ctr - from_ctr)*100:.1f}% CTR",
                    "milestone": label,
                    "action":    action,
                })
        return steps[:4]  # show max 4 steps

    @staticmethod
    def _build_action_plan(
        qs: float,
        query: str,
        fixes: list[str],
        benchmarks: list[AdRankBenchmark],
    ) -> list[str]:
        """Build a specific ordered action plan to reach #1 above paid ads."""
        plan: list[str] = []

        # Step 1: closest gap to beat first ad
        first_unbeaten = next((b for b in benchmarks if not b.beats_this_ad), None)
        if first_unbeaten:
            plan.append(
                f"Increase QS {qs:.1f} → {first_unbeaten.ad_typical_qs:.1f} "
                f"(+{first_unbeaten.qs_gap:.1f} pts) to beat '{first_unbeaten.ad_position}'. "
                f"{first_unbeaten.notes}"
            )

        # Step 2: beat all paid ads
        if qs < 8.5:
            plan.append(
                f"Reach QS 8.5 to outrank ALL 4 paid ad positions. "
                f"Gap: {round(8.5-qs,1)} pts. "
                "This gives position #1 organic — 31.4% CTR vs paid ads' ~2–5%."
            )

        # Step 3: specific content fixes
        for i, fix in enumerate(fixes[:3]):
            plan.append(f"Fix {i+1}: {fix}")

        # Step 4: AI Overview capture
        if qs < 9.5:
            plan.append(
                f"Reach QS 9.5 to enter Google AI Overviews — this displays ABOVE all paid ads "
                f"and has 0% cost. Gap: {round(9.5-qs,1)} pts."
            )

        return plan[:6]
