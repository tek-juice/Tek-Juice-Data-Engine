"""
DATA ENGINE — File Upload Validator
Validates uploaded files for type, size, and content integrity.
"""

from fastapi import UploadFile

from configs.constants import ALLOWED_MIME_TYPES, SourceType
from configs.settings import get_settings
from shared.exceptions.base import InvalidFileTypeError, FileTooLargeError

settings = get_settings()


def _detect_mime(content: bytes) -> str:
    """Detect MIME type from file content.

    Uses python-magic when libmagic is available (installed via
    `brew install libmagic` on macOS or `apt install libmagic1` on Linux).
    Falls back to a header-byte heuristic so the service still starts even
    when the system library is absent.
    """
    try:
        import magic as _magic
        return _magic.from_buffer(content, mime=True)
    except ImportError:
        # libmagic not installed — use a lightweight header-byte heuristic
        header = content[:8]
        if header[:4] == b"%PDF":
            return "application/pdf"
        if header[:4] == b"PK\x03\x04":
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        try:
            content[:512].decode("utf-8")
            return "text/plain"
        except UnicodeDecodeError:
            return "application/octet-stream"


async def validate_upload(file: UploadFile) -> tuple[bytes, SourceType]:
    """
    Validate an uploaded file.

    Checks:
    1. File size does not exceed MAX_UPLOAD_SIZE_MB
    2. MIME type is in the allowed list (detected from content, not just extension)

    Returns:
        Tuple of (file content bytes, detected SourceType)

    Raises:
        FileTooLargeError: File exceeds size limit.
        InvalidFileTypeError: MIME type not supported.
    """
    content = await file.read()
    await file.seek(0)

    # Size check
    if len(content) > settings.max_upload_size_bytes:
        raise FileTooLargeError(
            f"File size {len(content) / (1024*1024):.1f}MB "
            f"exceeds limit of {settings.max_upload_size_mb}MB."
        )

    # MIME type detection from content (not extension — safer)
    detected_mime = _detect_mime(content)

    if detected_mime not in ALLOWED_MIME_TYPES:
        raise InvalidFileTypeError(
            f"File type '{detected_mime}' is not supported. "
            f"Allowed types: {', '.join(ALLOWED_MIME_TYPES.keys())}"
        )

    source_type = ALLOWED_MIME_TYPES[detected_mime]
    return content, source_type


def validate_file_extension(filename: str) -> str:
    """Extract and validate the file extension."""
    ext_map = {
        ".pdf": "pdf",
        ".docx": "docx",
        ".doc": "docx",
        ".txt": "txt",
        ".md": "markdown",
        ".markdown": "markdown",
        ".json": "json",
        ".csv": "csv",
        ".html": "html",
        ".htm": "html",
    }
    if not filename:
        raise InvalidFileTypeError("Filename is required.")
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in ext_map:
        raise InvalidFileTypeError(f"Unsupported file extension: '{suffix}'")
    return ext_map[suffix]
