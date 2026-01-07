"""AWS ALB OIDC authentication middleware.

AWS ALB with OIDC authentication passes user information in headers:
- X-Amzn-Oidc-Identity: The user's unique identifier (sub claim)
- X-Amzn-Oidc-Data: JWT containing user claims (email, name, groups, etc.)
- X-Amzn-Oidc-Accesstoken: The access token (if needed for downstream calls)

This module parses these headers and creates/updates users in the database.
"""

import os
import json
import base64
import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Optional

from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import User

logger = logging.getLogger(__name__)

DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"


@dataclass
class ALBUser:
    """User information extracted from ALB OIDC headers."""

    id: int
    email: str
    display_name: str
    oidc_sub: str
    roles: list
    is_admin: bool


def decode_jwt_payload(jwt_token: str) -> dict:
    """Decode JWT payload without verification (ALB already verified)."""
    try:
        # JWT format: header.payload.signature
        parts = jwt_token.split(".")
        if len(parts) != 3:
            return {}

        # Decode payload (add padding if needed)
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding

        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception as e:
        logger.warning(f"Failed to decode JWT payload: {e}")
        return {}


def extract_user_from_headers(request: Request) -> Optional[dict]:
    """Extract user information from ALB OIDC headers."""
    # Get the identity (sub claim)
    oidc_identity = request.headers.get("X-Amzn-Oidc-Identity")

    # Get the JWT data with full claims
    oidc_data = request.headers.get("X-Amzn-Oidc-Data")

    if not oidc_identity or not oidc_data:
        return None

    # Decode JWT payload
    claims = decode_jwt_payload(oidc_data)

    if not claims:
        return None

    # Extract user info from claims
    email = claims.get("email", "")
    display_name = claims.get("name") or claims.get("preferred_username") or email.split("@")[0]

    # Extract roles/groups (varies by OIDC provider)
    roles = claims.get("groups", []) or claims.get("roles", []) or []
    if isinstance(roles, str):
        roles = [roles]

    return {
        "oidc_sub": oidc_identity,
        "email": email,
        "display_name": display_name,
        "roles": roles,
    }


def get_or_create_user(db: Session, user_info: dict) -> User:
    """Get existing user or create new one from OIDC info."""
    # Try to find by OIDC sub first
    user = db.query(User).filter(User.oidc_sub == user_info["oidc_sub"]).first()

    if not user:
        # Try by email
        user = db.query(User).filter(User.email == user_info["email"]).first()

        if user:
            # Update existing user with OIDC sub
            user.oidc_sub = user_info["oidc_sub"]
        else:
            # Create new user
            user = User(
                email=user_info["email"],
                display_name=user_info["display_name"],
                oidc_sub=user_info["oidc_sub"],
                roles=user_info["roles"],
                is_admin=False,
            )
            db.add(user)

    # Update last login and roles
    user.last_login = datetime.utcnow()
    user.roles = user_info["roles"]
    user.display_name = user_info["display_name"]

    db.commit()
    db.refresh(user)

    return user


def get_mock_user(db: Session) -> User:
    """Get or create mock user for development mode."""
    mock_email = "dev@netagent.local"
    user = db.query(User).filter(User.email == mock_email).first()

    if not user:
        user = User(
            email=mock_email,
            display_name="Development User",
            oidc_sub="dev-user-sub",
            roles=["admin"],
            is_admin=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    return user


async def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> ALBUser:
    """FastAPI dependency to get current authenticated user.

    In production, extracts user from ALB OIDC headers.
    In dev mode (DEV_MODE=true), returns a mock admin user.
    """
    # Development mode bypass
    if DEV_MODE:
        logger.debug("DEV_MODE enabled, using mock user")
        user = get_mock_user(db)
        return ALBUser(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            oidc_sub=user.oidc_sub,
            roles=user.roles,
            is_admin=user.is_admin,
        )

    # Extract user from headers
    user_info = extract_user_from_headers(request)

    if not user_info:
        logger.warning("No OIDC headers found in request")
        raise HTTPException(
            status_code=401,
            detail="Authentication required. No OIDC headers found.",
        )

    # Get or create user in database
    user = get_or_create_user(db, user_info)

    return ALBUser(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        oidc_sub=user.oidc_sub,
        roles=user.roles,
        is_admin=user.is_admin,
    )


async def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[ALBUser]:
    """FastAPI dependency for optional authentication.

    Returns user if authenticated, None otherwise.
    """
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None


def require_admin(user: ALBUser = Depends(get_current_user)) -> ALBUser:
    """FastAPI dependency to require admin access."""
    if not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Admin access required",
        )
    return user
