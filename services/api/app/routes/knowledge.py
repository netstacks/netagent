"""Knowledge base management routes."""

from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, KnowledgeBase, KnowledgeDocument
from netagent_core.auth import get_current_user, ALBUser
from netagent_core.utils import audit_log, AuditEventType

router = APIRouter()


class KnowledgeBaseCreate(BaseModel):
    name: str
    description: Optional[str] = None
    source_type: str  # 'confluence', 'manual', 'url'
    source_config: Optional[dict] = None


class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    source_config: Optional[dict] = None


class KnowledgeBaseResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    source_type: str
    source_config: Optional[dict]
    last_sync_at: Optional[datetime]
    sync_status: Optional[str]
    document_count: int
    chunk_count: int
    created_by: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True


class SearchRequest(BaseModel):
    query: str
    knowledge_base_ids: List[int]
    top_k: int = 5


@router.get("", response_model=dict)
async def list_knowledge_bases(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    limit: int = Query(default=50, le=100),
    offset: int = 0,
):
    """List all knowledge bases."""
    query = db.query(KnowledgeBase)
    total = query.count()
    kbs = query.order_by(KnowledgeBase.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "items": [KnowledgeBaseResponse.model_validate(kb) for kb in kbs],
        "total": total,
    }


@router.get("/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(
    kb_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get knowledge base by ID."""
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    return KnowledgeBaseResponse.model_validate(kb)


@router.post("", response_model=KnowledgeBaseResponse)
async def create_knowledge_base(
    data: KnowledgeBaseCreate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Create a new knowledge base."""
    kb = KnowledgeBase(
        name=data.name,
        description=data.description,
        source_type=data.source_type,
        source_config=data.source_config or {},
        created_by=user.id,
    )

    db.add(kb)
    db.commit()
    db.refresh(kb)

    audit_log(
        db,
        AuditEventType.KNOWLEDGE_CREATED,
        user=user,
        resource_type="knowledge_base",
        resource_id=kb.id,
        resource_name=kb.name,
        action="create",
    )

    return KnowledgeBaseResponse.model_validate(kb)


@router.put("/{kb_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    kb_id: int,
    data: KnowledgeBaseUpdate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Update a knowledge base."""
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(kb, key, value)

    db.commit()
    db.refresh(kb)

    audit_log(
        db,
        AuditEventType.KNOWLEDGE_UPDATED,
        user=user,
        resource_type="knowledge_base",
        resource_id=kb.id,
        resource_name=kb.name,
        action="update",
    )

    return KnowledgeBaseResponse.model_validate(kb)


@router.delete("/{kb_id}")
async def delete_knowledge_base(
    kb_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Delete a knowledge base."""
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    kb_name = kb.name
    db.delete(kb)
    db.commit()

    audit_log(
        db,
        AuditEventType.KNOWLEDGE_DELETED,
        user=user,
        resource_type="knowledge_base",
        resource_id=kb_id,
        resource_name=kb_name,
        action="delete",
    )

    return {"message": "Knowledge base deleted"}


@router.post("/{kb_id}/sync")
async def sync_knowledge_base(
    kb_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Trigger sync for a knowledge base."""
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    kb.sync_status = "pending"
    db.commit()

    # TODO: Queue sync task
    # from services.tasks import sync_knowledge_base
    # sync_knowledge_base.delay(kb_id)

    audit_log(
        db,
        AuditEventType.KNOWLEDGE_SYNC_STARTED,
        user=user,
        resource_type="knowledge_base",
        resource_id=kb.id,
        resource_name=kb.name,
        action="sync",
    )

    return {"message": "Sync started", "status": "pending"}


@router.get("/{kb_id}/documents", response_model=dict)
async def list_documents(
    kb_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    limit: int = Query(default=50, le=100),
    offset: int = 0,
):
    """List documents in a knowledge base."""
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    query = db.query(KnowledgeDocument).filter(KnowledgeDocument.knowledge_base_id == kb_id)
    total = query.count()
    docs = query.order_by(KnowledgeDocument.title).offset(offset).limit(limit).all()

    return {
        "items": [
            {
                "id": d.id,
                "source_id": d.source_id,
                "source_url": d.source_url,
                "title": d.title,
                "last_synced_at": d.last_synced_at.isoformat() if d.last_synced_at else None,
            }
            for d in docs
        ],
        "total": total,
    }


@router.post("/search")
async def search_knowledge(
    data: SearchRequest,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Search across knowledge bases using vector similarity."""
    # TODO: Implement vector search with pgvector
    # This requires embedding the query and searching knowledge_chunks

    return {
        "query": data.query,
        "results": [],
        "message": "Vector search not yet implemented",
    }
