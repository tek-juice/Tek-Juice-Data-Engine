"""
DATA ENGINE — Permission Helpers
Role-based and resource-level permission checks.
"""

from shared.authentication.jwt_handler import TokenPayload
from shared.exceptions.base import ForbiddenError


ROLE_HIERARCHY = {
    "viewer": 0,
    "editor": 1,
    "admin": 2,
}


def require_role(user: TokenPayload, minimum_role: str) -> None:
    """
    Raise ForbiddenError if user's role is below the minimum required.

    Args:
        user: Authenticated user payload.
        minimum_role: Lowest acceptable role ('viewer', 'editor', 'admin').
    """
    user_level = ROLE_HIERARCHY.get(user.role, 0)
    required_level = ROLE_HIERARCHY.get(minimum_role, 0)
    if user_level < required_level:
        raise ForbiddenError(
            f"Requires '{minimum_role}' role. Your role: '{user.role}'."
        )


def require_tenant_match(user: TokenPayload, resource_tenant_id: str) -> None:
    """
    Raise ForbiddenError if the resource belongs to a different tenant.
    Admin users bypass this check.
    """
    if not user.is_admin and str(user.tenant_id) != str(resource_tenant_id):
        raise ForbiddenError("You cannot access resources from another tenant.")


def can_write(user: TokenPayload) -> bool:
    """Return True if the user has write permissions (editor or admin)."""
    return ROLE_HIERARCHY.get(user.role, 0) >= ROLE_HIERARCHY["editor"]


def can_admin(user: TokenPayload) -> bool:
    """Return True if the user has admin permissions."""
    return user.role == "admin"
