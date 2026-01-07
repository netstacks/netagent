"""User management routes."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, User
from netagent_core.auth import get_current_user, require_admin, ALBUser

router = APIRouter()


class UserResponse(BaseModel):
    id: int
    email: str
    display_name: Optional[str]
    roles: list
    is_admin: bool
    last_login: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    is_admin: Optional[bool] = None
    roles: Optional[list] = None


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    user: ALBUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get current user information."""
    db_user = db.query(User).filter(User.id == user.id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse.model_validate(db_user)


@router.get("", response_model=dict)
async def list_users(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(require_admin),
    limit: int = Query(default=50, le=100),
    offset: int = 0,
):
    """List all users (admin only)."""
    query = db.query(User)
    total = query.count()
    users = query.order_by(User.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "items": [UserResponse.model_validate(u) for u in users],
        "total": total,
    }


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(require_admin),
):
    """Get user by ID (admin only)."""
    db_user = db.query(User).filter(User.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse.model_validate(db_user)


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    data: UserUpdate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(require_admin),
):
    """Update user (admin only)."""
    db_user = db.query(User).filter(User.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    if data.display_name is not None:
        db_user.display_name = data.display_name
    if data.is_admin is not None:
        db_user.is_admin = data.is_admin
    if data.roles is not None:
        db_user.roles = data.roles

    db.commit()
    db.refresh(db_user)

    return UserResponse.model_validate(db_user)
