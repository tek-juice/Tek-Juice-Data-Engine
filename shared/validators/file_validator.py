"""
DATA ENGINE — File Upload Validator
Validates uploaded files for type, size, and content integrity.
"""

import magic
from fastapi import UploadFile

from configs.constants import ALLOWED_MIME_TYPES, SourceType
from configs.settings import get_settings
from shared.exceptions.base import InvalidFileTypeError, FileTooLargeError

settings = get_settings()


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
    detected_mime = magic.from_buffer(content, mime=True)

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
