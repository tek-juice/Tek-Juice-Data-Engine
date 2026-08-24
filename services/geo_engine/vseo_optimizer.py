"""
DATA ENGINE — VSEO (Visual Search Engine Optimisation) Multi-Modal Optimizer
Pillar 2: Optimises images and video for AI-powered photo and video search.

Strategy:
  - AI image searches (Google Lens, Bing Visual, ChatGPT vision) rely on
    structured ImageObject / VideoObject Schema.org markup, descriptive
    alt-text, and captions to understand visual content without running
    their own vision inference on every image.
  - Video AI search (YouTube AI Summaries, Perplexity video answers) requires
    precise transcripts segmented with timestamps + VideoObject markup.
  - This module scores existing image/video content, generates Schema.org
    markup, and produces AI-ready alt-text and transcript chunks.

Schema.org types: ImageObject, VideoObject, Clip, MediaObject
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

SCHEMA_ORG = "https://schema.org"

# ── Scoring thresholds ────────────────────────────────────────────────────────
MIN_ALT_TEXT_WORDS = 5
MAX_ALT_TEXT_WORDS = 20
IDEAL_CAPTION_WORDS = 25
MIN_TRANSCRIPT_SEGMENT_WORDS = 10
MAX_TRANSCRIPT_SEGMENT_WORDS = 60

# Common decorative filenames — score zero for AI relevance
DECORATIVE_FILENAME_PATTERNS = re.compile(
    r'^(img|image|photo|pic|dsc|screenshot|untitled|banner|hero|bg|background)'
    r'[-_]?\d*\.(jpg|jpeg|png|webp|gif|svg)$',
    re.IGNORECASE,
)


# ── Image dataclasses ─────────────────────────────────────────────────────────

@dataclass
class ImageAsset:
    """A single image with all VSEO-relevant attributes."""
    url: str
    alt_text: str = ""
    caption: str = ""
    filename: str = ""
    width: int = 0
    height: int = 0
    file_size_kb: int = 0
    content_url: str = ""      # direct asset URL (may differ from page URL)
    license_url: str = ""
    author: str = ""
    # AI-generated description (optional — set by VseoOptimiser)
    ai_description: str = ""


@dataclass
class ImageVseoScore:
    """VSEO scoring result for a single image."""
    url: str
    alt_text_score: float       # 0–25
    caption_score: float        # 0–25
    filename_score: float       # 0–25
    technical_score: float      # 0–25 (resolution, file size)
    overall_score: float        # 0–100
    issues: list[str]
    recommendations: list[str]
    schema_org: dict[str, Any]  # ready-to-embed ImageObject JSON-LD
    optimised_alt_text: str     # AI-ready alt text


# ── Transcript dataclasses ────────────────────────────────────────────────────

@dataclass
class TranscriptSegment:
    """A timed segment of a video transcript."""
    start_seconds: float
    end_seconds: float
    text: str

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    def to_clip_schema(self, video_url: str, index: int) -> dict[str, Any]:
        """Build a Schema.org Clip entity for this segment."""
        return {
            "@type":              "Clip",
            "name":               f"Segment {index + 1}",
            "startOffset":        int(self.start_seconds),
            "endOffset":          int(self.end_seconds),
            "url":                f"{video_url}#t={int(self.start_seconds)}",
            "description":        self.text[:200],
        }


@dataclass
class VideoAsset:
    """A single video with all VSEO-relevant attributes."""
    url: str
    name: str = ""
    description: str = ""
    thumbnail_url: str = ""
    duration_iso: str = ""     # ISO 8601 e.g. "PT2M30S"
    duration_seconds: int = 0
    upload_date: str = ""      # ISO date
    transcript_segments: list[TranscriptSegment] = field(default_factory=list)
    author: str = ""
    embed_url: str = ""


@dataclass
class VideoVseoScore:
    """VSEO scoring result for a single video."""
    url: str
    metadata_score: float           # 0–30 (name, description, thumbnail, date)
    transcript_score: float         # 0–40 (segments, density, AI-readability)
    technical_score: float          # 0–30 (duration metadata, embed URL)
    overall_score: float            # 0–100
    issues: list[str]
    recommendations: list[str]
    schema_org: dict[str, Any]      # ready-to-embed VideoObject JSON-LD
    ai_summary: str                 # ≤ 40-word AI-scannable summary


# ── Main Optimizer ────────────────────────────────────────────────────────────

class VseoOptimiser:
    """
    Multi-modal VSEO analyser for images and videos.

    For AI photo-based queries, what matters is NOT just the pixel quality
    but the structured context attached to the image: precise alt text,
    keyword-rich captions, and ImageObject Schema.org markup.

    For AI video search, transcripts segmented into 30–60 second chunks
    with timestamp anchors allow AI engines to cite specific moments
    rather than the full video — dramatically increasing the chance of
    appearing in AI-generated video summaries.
    """

    # ── Image analysis ─────────────────────────────────────────────────────

    def score_image(self, image: ImageAsset) -> ImageVseoScore:
        """
        Score an image for VSEO readiness and generate Schema.org markup.

        Scoring dimensions (25 pts each):
          1. Alt text quality  — length, specificity, keyword presence
          2. Caption quality   — length, factual content
          3. Filename quality  — descriptive vs decorative pattern
          4. Technical quality — resolution, file size, content_url

        Returns ImageVseoScore with per-dimension scores, issues,
        recommendations, and a ready-to-embed ImageObject JSON-LD block.
        """
        issues: list[str] = []
        recs: list[str] = []

        # ─ Dimension 1: Alt text ──────────────────────────────────────────
        alt_score = self._score_alt_text(image.alt_text, issues, recs)

        # ─ Dimension 2: Caption ───────────────────────────────────────────
        cap_score = self._score_caption(image.caption, issues, recs)

        # ─ Dimension 3: Filename ──────────────────────────────────────────
        fn_score = self._score_filename(image.filename, issues, recs)

        # ─ Dimension 4: Technical ─────────────────────────────────────────
        tech_score = self._score_image_technical(image, issues, recs)

        overall = alt_score + cap_score + fn_score + tech_score

        # Optimised alt text (use caption as base if alt is missing/weak)
        optimised_alt = self._optimise_alt_text(image)

        schema = self._build_image_schema(image, optimised_alt)

        logger.debug("vseo_image_scored", url=image.url, score=round(overall, 1))
        return ImageVseoScore(
            url=image.url,
            alt_text_score=round(alt_score, 1),
            caption_score=round(cap_score, 1),
            filename_score=round(fn_score, 1),
            technical_score=round(tech_score, 1),
            overall_score=round(overall, 1),
            issues=issues,
            recommendations=recs,
            schema_org=schema,
            optimised_alt_text=optimised_alt,
        )

    def score_video(self, video: VideoAsset) -> VideoVseoScore:
        """
        Score a video for VSEO readiness and generate Schema.org markup.

        Scoring dimensions:
          1. Metadata (0–30) — name, description, thumbnail, upload date
          2. Transcript (0–40) — segments, density, timing precision
          3. Technical (0–30) — duration ISO, embed URL

        The transcript is the highest-weighted dimension because AI video
        search (e.g. Perplexity, YouTube AI summaries) cites specific
        timestamped segments rather than whole videos.
        """
        issues: list[str] = []
        recs: list[str] = []

        meta_score = self._score_video_metadata(video, issues, recs)
        trans_score = self._score_transcript(video.transcript_segments, issues, recs)
        tech_score = self._score_video_technical(video, issues, recs)

        overall = meta_score + trans_score + tech_score
        ai_summary = self._build_video_ai_summary(video)
        schema = self._build_video_schema(video, ai_summary)

        logger.debug("vseo_video_scored", url=video.url, score=round(overall, 1))
        return VideoVseoScore(
            url=video.url,
            metadata_score=round(meta_score, 1),
            transcript_score=round(trans_score, 1),
            technical_score=round(tech_score, 1),
            overall_score=round(overall, 1),
            issues=issues,
            recommendations=recs,
            schema_org=schema,
            ai_summary=ai_summary,
        )

    def score_page_images(self, images: list[ImageAsset]) -> dict[str, Any]:
        """
        Score all images on a page and return an aggregate report.
        Returns page-level summary + per-image scores.
        """
        scores = [self.score_image(img) for img in images]
        if not scores:
            return {"page_vseo_score": 0.0, "images": [], "summary": []}

        avg_score = sum(s.overall_score for s in scores) / len(scores)
        critical = [s for s in scores if s.overall_score < 40]
        all_recs: list[str] = []
        seen: set[str] = set()
        for s in scores:
            for r in s.recommendations:
                if r not in seen:
                    seen.add(r)
                    all_recs.append(r)

        return {
            "page_vseo_score": round(avg_score, 1),
            "total_images": len(scores),
            "critical_count": len(critical),
            "images": [
                {
                    "url":              s.url,
                    "overall_score":    s.overall_score,
                    "alt_text_score":   s.alt_text_score,
                    "caption_score":    s.caption_score,
                    "filename_score":   s.filename_score,
                    "technical_score":  s.technical_score,
                    "issues":           s.issues,
                    "optimised_alt":    s.optimised_alt_text,
                    "schema_org":       s.schema_org,
                }
                for s in scores
            ],
            "page_recommendations": all_recs[:8],
        }

    # ── Private: image scoring ─────────────────────────────────────────────

    @staticmethod
    def _score_alt_text(alt: str, issues: list[str], recs: list[str]) -> float:
        if not alt or not alt.strip():
            issues.append("Missing alt text — AI engines cannot understand image content.")
            recs.append("Add descriptive alt text (5–20 words) describing what the image shows.")
            return 0.0

        words = len(alt.split())
        if words < MIN_ALT_TEXT_WORDS:
            issues.append(f"Alt text too short ({words} words) — insufficient for AI photo search.")
            recs.append(f"Expand alt text to {MIN_ALT_TEXT_WORDS}–{MAX_ALT_TEXT_WORDS} words with specific details.")
            return 8.0

        if words > MAX_ALT_TEXT_WORDS:
            issues.append(f"Alt text too long ({words} words) — exceeds AI readability target.")
            recs.append(f"Trim alt text to under {MAX_ALT_TEXT_WORDS} words.")
            return 15.0

        # Penalise filler starts
        lower = alt.lower()
        if any(lower.startswith(p) for p in ("image of", "picture of", "photo of", "icon of")):
            recs.append("Remove redundant 'image of / picture of' prefix from alt text.")
            return 18.0

        return 25.0

    @staticmethod
    def _score_caption(caption: str, issues: list[str], recs: list[str]) -> float:
        if not caption or not caption.strip():
            recs.append(
                "Add a visible image caption — AI engines use captions as context "
                "for visual search indexing."
            )
            return 5.0

        words = len(caption.split())
        if words < 5:
            issues.append(f"Caption too short ({words} words).")
            recs.append(f"Expand caption to ~{IDEAL_CAPTION_WORDS} words with factual context.")
            return 10.0

        return 25.0

    @staticmethod
    def _score_filename(filename: str, issues: list[str], recs: list[str]) -> float:
        if not filename:
            return 15.0  # Unknown — neutral

        if DECORATIVE_FILENAME_PATTERNS.match(filename):
            issues.append(
                f"Filename '{filename}' is a generic/decorative pattern — "
                "AI crawlers use filenames as content signals."
            )
            recs.append("Rename image files to describe their content (e.g., 'team-meeting-2024.jpg').")
            return 0.0

        # Reward hyphenated descriptive names
        if "-" in filename or "_" in filename:
            return 25.0

        return 15.0

    @staticmethod
    def _score_image_technical(
        image: ImageAsset, issues: list[str], recs: list[str]
    ) -> float:
        score = 0.0

        # Resolution
        if image.width >= 800 and image.height >= 600:
            score += 10.0
        elif image.width > 0:
            issues.append(
                f"Low resolution ({image.width}×{image.height}) — "
                "AI photo search prefers high-resolution images (≥ 800×600)."
            )
            recs.append("Use images of at least 800×600 px for reliable AI visual search indexing.")
        else:
            score += 5.0  # Unknown — partial credit

        # File size (proxy for quality — very small files are often compressed artifacts)
        if 0 < image.file_size_kb < 10:
            issues.append("Image file size is very small (<10 KB) — may be a thumbnail or artifact.")
            recs.append("Use full-resolution source images (ideally 50–500 KB for web).")
        elif image.file_size_kb >= 10 or image.file_size_kb == 0:
            score += 10.0

        # content_url present
        if image.content_url or image.url:
            score += 5.0

        return min(25.0, score)

    # ── Private: image helpers ─────────────────────────────────────────────

    @staticmethod
    def _optimise_alt_text(image: ImageAsset) -> str:
        """
        Return the best available AI-ready alt text for an image.
        Priority: ai_description > existing alt text (if good) > caption excerpt
        """
        if image.ai_description and len(image.ai_description.split()) >= MIN_ALT_TEXT_WORDS:
            words = image.ai_description.split()
            return " ".join(words[:MAX_ALT_TEXT_WORDS])

        alt = (image.alt_text or "").strip()
        words = alt.split()
        if MIN_ALT_TEXT_WORDS <= len(words) <= MAX_ALT_TEXT_WORDS:
            if not alt.lower().startswith(("image of", "picture of", "photo of")):
                return alt

        if image.caption:
            cap_words = image.caption.split()[:MAX_ALT_TEXT_WORDS]
            return " ".join(cap_words)

        return alt or image.filename.replace("-", " ").replace("_", " ").rsplit(".", 1)[0]

    @staticmethod
    def _build_image_schema(image: ImageAsset, optimised_alt: str) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "@context":   SCHEMA_ORG,
            "@type":      "ImageObject",
            "url":        image.url,
            "description": optimised_alt,
        }
        if image.content_url:
            schema["contentUrl"] = image.content_url
        if image.caption:
            schema["caption"] = image.caption
        if image.width and image.height:
            schema["width"] = image.width
            schema["height"] = image.height
        if image.author:
            schema["author"] = {"@type": "Person", "name": image.author}
        if image.license_url:
            schema["license"] = image.license_url
        return schema

    # ── Private: video scoring ─────────────────────────────────────────────

    @staticmethod
    def _score_video_metadata(
        video: VideoAsset, issues: list[str], recs: list[str]
    ) -> float:
        score = 0.0

        if video.name and len(video.name) >= 5:
            score += 8.0
        else:
            issues.append("Video missing a descriptive name/title.")
            recs.append("Add a specific video title (≥ 5 words) for AI video search indexing.")

        if video.description and len(video.description.split()) >= 10:
            score += 10.0
        else:
            issues.append("Video description is missing or too short.")
            recs.append("Add a 50+ word video description covering the topic, speaker, and key points.")

        if video.thumbnail_url:
            score += 7.0
        else:
            recs.append("Add a high-quality thumbnail URL — AI engines use thumbnails for visual context.")

        if video.upload_date:
            score += 5.0

        return min(30.0, score)

    @staticmethod
    def _score_transcript(
        segments: list[TranscriptSegment], issues: list[str], recs: list[str]
    ) -> float:
        if not segments:
            issues.append(
                "No transcript segments found — AI video search cannot cite specific moments."
            )
            recs.append(
                "Add a word-level or sentence-level timed transcript. "
                "Segment into 30–60 second chunks with start/end timestamps."
            )
            return 0.0

        score = 10.0  # base for having any transcript

        # Check segment density (words per segment)
        avg_words = (
            sum(len(s.text.split()) for s in segments) / len(segments)
            if segments else 0
        )
        if MIN_TRANSCRIPT_SEGMENT_WORDS <= avg_words <= MAX_TRANSCRIPT_SEGMENT_WORDS:
            score += 15.0
        elif avg_words > MAX_TRANSCRIPT_SEGMENT_WORDS:
            recs.append(
                f"Transcript segments average {avg_words:.0f} words — break into "
                f"shorter {MIN_TRANSCRIPT_SEGMENT_WORDS}–{MAX_TRANSCRIPT_SEGMENT_WORDS} word chunks."
            )
            score += 7.0
        else:
            recs.append("Transcript segments are very short — merge adjacent segments for context.")
            score += 5.0

        # Timestamp precision
        has_precise_times = any(s.start_seconds > 0 or s.end_seconds > 0 for s in segments)
        if has_precise_times:
            score += 10.0
        else:
            issues.append("Transcript segments lack timestamps — AI cannot link citations to video moments.")
            recs.append("Add precise start/end timestamps (in seconds) to every transcript segment.")

        # Coverage: at least 5 segments for reasonable granularity
        if len(segments) >= 5:
            score += 5.0
        else:
            recs.append(f"Only {len(segments)} transcript segment(s) — add more for finer AI indexing granularity.")

        return min(40.0, score)

    @staticmethod
    def _score_video_technical(
        video: VideoAsset, issues: list[str], recs: list[str]
    ) -> float:
        score = 0.0

        if video.duration_iso:
            score += 10.0
        else:
            recs.append("Add ISO 8601 duration (e.g. 'PT2M30S') to VideoObject schema.")

        if video.embed_url:
            score += 10.0
        else:
            recs.append("Add an embedUrl to VideoObject — allows AI to surface the video in rich results.")

        if video.author:
            score += 5.0

        if video.duration_seconds >= 30:
            score += 5.0
        elif video.duration_seconds > 0:
            issues.append(f"Video is very short ({video.duration_seconds}s) — may not be indexed for AI search.")

        return min(30.0, score)

    # ── Private: video helpers ─────────────────────────────────────────────

    @staticmethod
    def _build_video_ai_summary(video: VideoAsset) -> str:
        """
        Build a ≤ 40-word AI-scannable summary from transcript + metadata.
        Pattern: "{Name} — {description first sentence}. {transcript excerpt}."
        """
        name_part = video.name or "Video"
        desc_sent = (video.description or "").split(".")[0].strip()
        if len(desc_sent.split()) > 20:
            desc_sent = " ".join(desc_sent.split()[:20])

        trans_excerpt = ""
        if video.transcript_segments:
            first_seg = video.transcript_segments[0].text
            trans_excerpt = " ".join(first_seg.split()[:15])

        summary = f"{name_part} — {desc_sent}."
        if trans_excerpt:
            summary += f" {trans_excerpt}."

        words = summary.split()
        if len(words) > 40:
            summary = " ".join(words[:40]) + "."
        return summary

    def _build_video_schema(self, video: VideoAsset, ai_summary: str) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "@context":   SCHEMA_ORG,
            "@type":      "VideoObject",
            "name":       video.name or "Video",
            "description": ai_summary,
            "uploadDate": video.upload_date or "",
        }
        if video.url:
            schema["url"] = video.url
        if video.thumbnail_url:
            schema["thumbnailUrl"] = video.thumbnail_url
        if video.duration_iso:
            schema["duration"] = video.duration_iso
        if video.embed_url:
            schema["embedUrl"] = video.embed_url
        if video.author:
            schema["author"] = {"@type": "Person", "name": video.author}

        # Add Clip entities for transcript segments (highest AI citation value)
        if video.transcript_segments:
            schema["hasPart"] = [
                seg.to_clip_schema(video.url, idx)
                for idx, seg in enumerate(video.transcript_segments[:20])
            ]
            # Full transcript as text
            full_transcript = " ".join(s.text for s in video.transcript_segments)
            schema["transcript"] = full_transcript[:5000]

        return schema
