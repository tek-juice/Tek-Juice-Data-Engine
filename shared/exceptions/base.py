"""
DATA ENGINE — Custom Exception Hierarchy
All services raise these typed exceptions.
The shared error handler converts them to HTTP responses.
"""

from configs.constants import ErrorCode


class DataEngineError(Exception):
    """Base exception for all DATA ENGINE errors."""

    status_code: int = 500
    error_code: str = ErrorCode.INTERNAL_ERROR
    message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None, details: dict | None = None) -> None:
        self.message = message or self.__class__.message
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict:
        return {
            "code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


# ── 400 Bad Request ───────────────────────────────────────────────────────────

class ValidationError(DataEngineError):
    status_code = 400
    error_code = ErrorCode.VALIDATION_ERROR
    message = "Validation failed."


class InvalidFileTypeError(ValidationError):
    message = "Unsupported file type."


class FileTooLargeError(ValidationError):
    message = "File exceeds maximum allowed size."


class InvalidChunkConfigError(ValidationError):
    message = "Invalid chunking configuration."


# ── 401 Unauthorised ──────────────────────────────────────────────────────────

class UnauthorisedError(DataEngineError):
    status_code = 401
    error_code = ErrorCode.UNAUTHORISED
    message = "Authentication required."


class InvalidTokenError(UnauthorisedError):
    message = "Token is invalid or expired."


class InvalidAPIKeyError(UnauthorisedError):
    message = "API key is invalid or has been revoked."


# ── 403 Forbidden ─────────────────────────────────────────────────────────────

class ForbiddenError(DataEngineError):
    status_code = 403
    error_code = ErrorCode.FORBIDDEN
    message = "You do not have permission to perform this action."


# ── 404 Not Found ─────────────────────────────────────────────────────────────

class NotFoundError(DataEngineError):
    status_code = 404
    error_code = ErrorCode.NOT_FOUND
    message = "Resource not found."


class DocumentNotFoundError(NotFoundError):
    message = "Document not found."


class TenantNotFoundError(NotFoundError):
    message = "Tenant not found."


class EmbeddingNotFoundError(NotFoundError):
    message = "Embedding not found."


# ── 409 Conflict ──────────────────────────────────────────────────────────────

class AlreadyExistsError(DataEngineError):
    status_code = 409
    error_code = ErrorCode.ALREADY_EXISTS
    message = "Resource already exists."


# ── 429 Rate Limit ────────────────────────────────────────────────────────────

class RateLimitError(DataEngineError):
    status_code = 429
    error_code = ErrorCode.RATE_LIMIT_EXCEEDED
    message = "Rate limit exceeded. Please slow down."


# ── 500 Internal Errors ───────────────────────────────────────────────────────

class DatabaseError(DataEngineError):
    status_code = 500
    error_code = ErrorCode.DATABASE_ERROR
    message = "A database error occurred."


class StorageError(DataEngineError):
    status_code = 500
    error_code = ErrorCode.STORAGE_ERROR
    message = "A storage error occurred."


class ProcessingError(DataEngineError):
    status_code = 500
    error_code = ErrorCode.PROCESSING_ERROR
    message = "An error occurred during processing."


# ── 503 Service Errors ────────────────────────────────────────────────────────

class EmbeddingProviderError(DataEngineError):
    status_code = 503
    error_code = ErrorCode.EMBEDDING_PROVIDER_ERROR
    message = "Embedding provider is unavailable."


class ServiceUnavailableError(DataEngineError):
    status_code = 503
    error_code = ErrorCode.SERVICE_UNAVAILABLE
    message = "Service is temporarily unavailable."
