import logging
import secrets
from typing import Optional

from fastapi import Header, HTTPException

from .config import settings

logger = logging.getLogger("guardrails.security")

if not settings.ADMIN_API_KEY:
    logger.warning(
        "ADMIN_API_KEY is not set — /admin and /audit are open. "
        "Set ADMIN_API_KEY before deploying anywhere shared."
    )


def require_admin(x_admin_key: Optional[str] = Header(default=None)) -> None:
    """Guards admin/audit routes. When ADMIN_API_KEY is unset, routes stay open
    (dev mode); when set, a matching ``X-Admin-Key`` header is required."""
    if not settings.ADMIN_API_KEY:
        return
    if not x_admin_key or not secrets.compare_digest(x_admin_key, settings.ADMIN_API_KEY):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "message": "Missing or invalid admin credentials. Expected header 'X-Admin-Key'.",
                    "type": "authentication_error",
                    "code": "invalid_admin_key",
                }
            },
        )
