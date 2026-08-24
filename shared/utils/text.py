"""
DATA ENGINE — Text Utility Functions
Cleaning, normalisation, and language detection helpers
used across ingestion, chunking, and preprocessing.
"""

import re
import unicodedata
from typing import Optional


def clean_text(text: str) -> str:
    """
    Full text cleaning pipeline:
    - Strip HTML/XML tags
    - Normalise Unicode (NFC)
    - Normalise whitespace
    - Remove null bytes and control characters
    """
    text = strip_html(text)
    text = normalize_unicode(text)
    text = remove_control_chars(text)
    text = normalize_whitespace(text)
    return text.strip()


def strip_html(text: str) -> str:
    """Remove HTML and XML tags from text."""
    return re.sub(r"<[^>]+>", " ", text)


def normalize_unicode(text: str) -> str:
    """Normalise Unicode to NFC form."""
    return unicodedata.normalize("NFC", text)


def remove_control_chars(text: str) -> str:
    """Remove null bytes and non-printable control characters."""
    # Keep newlines, tabs, carriage returns — remove everything else
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def normalize_whitespace(text: str) -> str:
    """Collapse multiple spaces/tabs into single space. Preserve newlines."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def truncate_text(text: str, max_chars: int, suffix: str = "...") -> str:
    """Truncate text to max_chars, appending suffix if truncated."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(suffix)].rstrip() + suffix


def count_words(text: str) -> int:
    """Count whitespace-separated words in text."""
    return len(text.split())


def count_sentences(text: str) -> int:
    """Estimate sentence count using punctuation heuristics."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return len([s for s in sentences if s])


def extract_urls(text: str) -> list[str]:
    """Extract all URLs from text."""
    pattern = r"https?://[^\s<>\"{}|\\^`\[\]]+"
    return re.findall(pattern, text)


def remove_urls(text: str) -> str:
    """Remove all URLs from text."""
    return re.sub(r"https?://[^\s<>\"{}|\\^`\[\]]+", "", text)


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    text = normalize_unicode(text).lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-")


def split_into_sentences(text: str) -> list[str]:
    """Split text into sentences using simple punctuation rules."""
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [s.strip() for s in sentences if s.strip()]


def detect_language_simple(text: str) -> str:
    """
    Very lightweight language detection based on common word frequency.
    For production use, replace with langdetect or spaCy's language detector.
    Returns ISO 639-1 code (e.g. 'en', 'fr', 'de') or 'unknown'.
    """
    # Simple heuristic: check for high-frequency English stopwords
    english_stopwords = {"the", "is", "are", "and", "of", "to", "a", "in", "that", "it"}
    words = set(text.lower().split()[:100])
    overlap = words & english_stopwords
    if len(overlap) >= 3:
        return "en"
    return "unknown"
