"""
DATA ENGINE — Text Extractor
Extracts raw text from supported file formats (PDF, DOCX, TXT, JSON, CSV, HTML).
"""

import json
import csv
import io
import structlog
from configs.constants import SourceType
from shared.utils.text import clean_text

logger = structlog.get_logger(__name__)


def extract_text(content: bytes, source_type: str) -> str:
    """
    Route content to the appropriate extractor based on source_type.

    Args:
        content: Raw file bytes.
        source_type: One of the SourceType enum values.

    Returns:
        Cleaned plain text string.
    """
    extractors = {
        SourceType.PDF.value:      extract_pdf,
        SourceType.DOCX.value:     extract_docx,
        SourceType.TXT.value:      extract_txt,
        SourceType.MARKDOWN.value: extract_txt,
        SourceType.JSON.value:     extract_json,
        SourceType.CSV.value:      extract_csv,
        SourceType.HTML.value:     extract_html,
    }
    extractor = extractors.get(source_type)
    if not extractor:
        raise ValueError(f"No extractor available for source_type: {source_type}")

    raw = extractor(content)
    return clean_text(raw)


def extract_pdf(content: bytes) -> str:
    """Extract text from a PDF file using PyPDF2."""
    import PyPDF2
    reader = PyPDF2.PdfReader(io.BytesIO(content))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def extract_docx(content: bytes) -> str:
    """Extract text from a DOCX file."""
    from docx import Document
    doc = Document(io.BytesIO(content))
    paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
    return "\n\n".join(paragraphs)


def extract_txt(content: bytes) -> str:
    """Decode plain text or markdown file."""
    return content.decode("utf-8", errors="replace")


def extract_json(content: bytes) -> str:
    """Flatten JSON structure into readable text."""
    data = json.loads(content.decode("utf-8"))
    return _flatten_json(data)


def _flatten_json(obj, prefix: str = "") -> str:
    """Recursively convert JSON to key: value text lines."""
    lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            lines.append(_flatten_json(v, f"{prefix}{k}: " if not prefix else f"{prefix}{k}: "))
    elif isinstance(obj, list):
        for item in obj:
            lines.append(_flatten_json(item, prefix))
    else:
        lines.append(f"{prefix}{obj}")
    return "\n".join(lines)


def extract_csv(content: bytes) -> str:
    """Extract CSV as tab-separated text rows."""
    reader = csv.reader(io.StringIO(content.decode("utf-8", errors="replace")))
    rows = []
    for row in reader:
        rows.append(" | ".join(row))
    return "\n".join(rows)


def extract_html(content: bytes) -> str:
    """Strip HTML tags and extract visible text."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(content, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator="\n")
