import logging
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)


@dataclass
class TherapistContext:
    therapist_id: str


def _extract_therapist_id(payload: dict) -> str | None:
    for key in ("sub", "therapistId", "userId", "id"):
        v = payload.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


async def get_current_therapist(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    settings: Settings = Depends(get_settings),
) -> TherapistContext:
    if settings.skip_therapist_jwt:
        tid = (settings.dev_therapist_id or "").strip() or "anonymous"
        logger.warning(
            "SKIP_THERAPIST_JWT is enabled (disable for production); caller id=%s",
            tid[:16] if len(tid) > 16 else tid,
        )
        return TherapistContext(therapist_id=tid)

    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not settings.jwt_secret.strip():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_SECRET is not configured",
        )
    token = credentials.credentials
    try:
        options = {"require": ["exp"]}
        kwargs: dict = {
            "algorithms": [settings.jwt_algorithm],
            "options": options,
        }
        if settings.jwt_issuer:
            kwargs["issuer"] = settings.jwt_issuer
        if settings.jwt_audience:
            kwargs["audience"] = settings.jwt_audience
        payload = jwt.decode(token, settings.jwt_secret, **kwargs)
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    therapist_id = _extract_therapist_id(payload)
    if not therapist_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has no subject (sub) or therapist id claim",
        )
    return TherapistContext(therapist_id=therapist_id)
