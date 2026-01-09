"""Memory management API routes."""

import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, Memory, User
from netagent_core.auth import get_current_user, ALBUser
from netagent_core.memory import MemoryService

logger = logging.getLogger(__name__)

router = APIRouter()


# Pydantic models
class MemoryCreate(BaseModel):
    """Create a new memory."""
    content: str
    memory_type: str  # preference, fact, instruction
    category: Optional[str] = None
    scope: str = "user"  # user, agent, global
    agent_id: Optional[int] = None  # Required if scope is "agent"


class MemoryResponse(BaseModel):
    """Response model for a memory."""
    id: int
    content: str
    memory_type: str
    category: Optional[str]
    confidence: float
    is_user_specific: bool
    is_agent_specific: bool
    access_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MemoryRecallResponse(BaseModel):
    """Response model for recalled memory."""
    id: int
    content: str
    memory_type: str
    category: Optional[str]
    confidence: float
    scope: str


@router.get("/recall", response_model=List[MemoryRecallResponse])
async def recall_memories(
    query: str = Query(..., description="Search query for relevant memories"),
    category: Optional[str] = Query(None, description="Filter by category"),
    memory_type: Optional[str] = Query(None, description="Filter by type (preference, fact, instruction)"),
    limit: int = Query(10, le=50, description="Maximum results to return"),
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Recall relevant memories for the current user.

    Searches through user-specific, agent-specific, and global memories
    to find relevant matches based on the query.
    """
    # Get user from database
    db_user = db.query(User).filter(User.email == user.email).first()
    user_id = db_user.id if db_user else None

    service = MemoryService(db)

    memories = service.recall_memories(
        query=query,
        user_id=user_id,
        memory_type=memory_type,
        category=category,
        limit=limit,
        include_global=True,
    )

    return [
        MemoryRecallResponse(
            id=m.id,
            content=m.content,
            memory_type=m.memory_type,
            category=m.category,
            confidence=m.confidence,
            scope="user" if m.user_id else ("agent" if m.agent_id else "global"),
        )
        for m in memories
    ]


@router.post("/", response_model=MemoryResponse)
async def create_memory(
    request: MemoryCreate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Manually create a new memory.

    Memories can be scoped to:
    - user: Only visible to the current user
    - agent: Visible to a specific agent (requires agent_id)
    - global: Visible to all users and agents
    """
    # Get user from database
    db_user = db.query(User).filter(User.email == user.email).first()
    user_id = db_user.id if db_user else None

    # Validate scope
    if request.scope == "agent" and not request.agent_id:
        raise HTTPException(
            status_code=400,
            detail="agent_id is required when scope is 'agent'"
        )

    # Determine scoping
    memory_user_id = user_id if request.scope == "user" else None
    memory_agent_id = request.agent_id if request.scope == "agent" else None

    service = MemoryService(db)

    memory = service.store_memory(
        content=request.content,
        memory_type=request.memory_type,
        category=request.category,
        user_id=memory_user_id,
        agent_id=memory_agent_id,
        created_by=user_id,
    )

    return MemoryResponse(
        id=memory.id,
        content=memory.content,
        memory_type=memory.memory_type,
        category=memory.category,
        confidence=memory.confidence,
        is_user_specific=memory.user_id is not None,
        is_agent_specific=memory.agent_id is not None,
        access_count=memory.access_count,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )


@router.get("/preferences", response_model=List[MemoryResponse])
async def get_my_preferences(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get the current user's stored preferences.

    Returns all memories of type 'preference' that are scoped to the current user.
    """
    # Get user from database
    db_user = db.query(User).filter(User.email == user.email).first()
    if not db_user:
        return []

    service = MemoryService(db)
    preferences = service.get_user_preferences(db_user.id)

    return [
        MemoryResponse(
            id=p.id,
            content=p.content,
            memory_type=p.memory_type,
            category=p.category,
            confidence=p.confidence,
            is_user_specific=True,
            is_agent_specific=False,
            access_count=p.access_count,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in preferences
    ]


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get a specific memory by ID."""
    memory = db.query(Memory).filter(Memory.id == memory_id).first()

    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    # Check access - users can only see their own memories, global, or agent memories
    db_user = db.query(User).filter(User.email == user.email).first()
    user_id = db_user.id if db_user else None

    is_global = memory.user_id is None and memory.agent_id is None
    is_own = memory.user_id == user_id
    is_admin = db_user.is_admin if db_user else False

    if not (is_global or is_own or is_admin):
        raise HTTPException(status_code=403, detail="Access denied")

    return MemoryResponse(
        id=memory.id,
        content=memory.content,
        memory_type=memory.memory_type,
        category=memory.category,
        confidence=memory.confidence,
        is_user_specific=memory.user_id is not None,
        is_agent_specific=memory.agent_id is not None,
        access_count=memory.access_count,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )


@router.delete("/{memory_id}")
async def forget_memory(
    memory_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Delete (forget) a memory.

    Users can only delete their own memories unless they are admins.
    """
    memory = db.query(Memory).filter(Memory.id == memory_id).first()

    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    # Get user from database
    db_user = db.query(User).filter(User.email == user.email).first()
    user_id = db_user.id if db_user else None
    is_admin = db_user.is_admin if db_user else False

    # Only allow deleting own memories or if admin
    if memory.user_id != user_id and not is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    service = MemoryService(db)
    service.forget_memory(memory_id)

    logger.info(f"Memory {memory_id} forgotten by {user.email}")

    return {"status": "forgotten", "memory_id": memory_id}


@router.get("/agent/{agent_id}/knowledge", response_model=List[MemoryResponse])
async def get_agent_knowledge(
    agent_id: int,
    category: Optional[str] = Query(None, description="Filter by category"),
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get knowledge accumulated by a specific agent.

    Returns all 'fact' type memories scoped to the agent.
    """
    service = MemoryService(db)
    knowledge = service.get_agent_knowledge(agent_id, category=category)

    return [
        MemoryResponse(
            id=k.id,
            content=k.content,
            memory_type=k.memory_type,
            category=k.category,
            confidence=k.confidence,
            is_user_specific=False,
            is_agent_specific=True,
            access_count=k.access_count,
            created_at=k.created_at,
            updated_at=k.updated_at,
        )
        for k in knowledge
    ]
