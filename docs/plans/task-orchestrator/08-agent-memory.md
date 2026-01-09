# Phase 8: Agent Memory System

## Overview

Add persistent memory to NetAgent so agents can remember:
- **User preferences** - how users like results formatted, delivery preferences, etc.
- **Learned facts** - information discovered during tasks (e.g., "router-01 runs Junos 21.4")
- **Session summaries** - condensed learnings from completed sessions

Memory is scoped by:
- **User** - preferences specific to a user
- **Agent** - knowledge an agent accumulates across all users
- **Global** - facts available to all agents

---

## Task 8.1: Create Memory Models

**Files:**
- Create: `shared/netagent_core/db/models/memory.py`
- Modify: `shared/netagent_core/db/models.py` (add imports)
- Modify: `shared/netagent_core/db/__init__.py` (export models)

### Step 1: Create memory models

Create `shared/netagent_core/db/models/memory.py`:

```python
"""Agent memory models for persistent knowledge."""

from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    ForeignKey,
    Float,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from .database import Base


class Memory(Base):
    """Persistent memory entries for agents.

    Memories are facts, preferences, or learnings that persist across sessions.
    They can be scoped to a user, agent, or be global.
    """

    __tablename__ = "memories"

    id = Column(Integer, primary_key=True)

    # Memory content
    content = Column(Text, nullable=False)  # The actual memory text
    memory_type = Column(String(30), nullable=False, index=True)  # preference, fact, summary, instruction

    # Scoping - at least one should be set, or none for global
    user_id = Column(Integer, ForeignKey("users.id"), index=True)  # User-specific memory
    agent_id = Column(Integer, ForeignKey("agents.id"), index=True)  # Agent-specific memory
    # If both null = global memory accessible to all

    # Source tracking
    source_session_id = Column(Integer, ForeignKey("agent_sessions.id"))  # Where this was learned
    source_job_id = Column(Integer, ForeignKey("jobs.id"))  # If learned during a job

    # Metadata
    category = Column(String(50), index=True)  # e.g., "device_info", "user_preference", "network_topology"
    tags = Column(JSONB, default=list)  # Searchable tags
    confidence = Column(Float, default=1.0)  # How confident we are (0-1)

    # Vector embedding for semantic search
    embedding = Column(Vector(768))  # Same dimension as knowledge chunks

    # Lifecycle
    is_active = Column(Boolean, default=True, index=True)
    expires_at = Column(DateTime)  # Optional expiration
    access_count = Column(Integer, default=0)  # Track usage
    last_accessed_at = Column(DateTime)

    # Audit
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    agent = relationship("Agent", foreign_keys=[agent_id])
    source_session = relationship("AgentSession")
    source_job = relationship("Job")
    creator = relationship("User", foreign_keys=[created_by])

    __table_args__ = (
        Index("idx_memories_scope", "user_id", "agent_id", "is_active"),
        Index("idx_memories_category", "category", "is_active"),
        Index("idx_memories_embedding", embedding, postgresql_using="ivfflat",
              postgresql_with={"lists": 100}, postgresql_ops={"embedding": "vector_cosine_ops"}),
    )


class SessionSummary(Base):
    """Condensed summary of a completed session.

    Auto-generated when sessions complete, capturing key learnings
    that might be useful for future sessions.
    """

    __tablename__ = "session_summaries"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("agent_sessions.id", ondelete="CASCADE"),
                        unique=True, nullable=False)

    # Summary content
    summary = Column(Text, nullable=False)  # Natural language summary
    key_actions = Column(JSONB, default=list)  # List of important actions taken
    key_findings = Column(JSONB, default=list)  # List of facts discovered
    tools_used = Column(JSONB, default=list)  # Tools that were used

    # Extracted memories (references to Memory records created from this session)
    extracted_memory_ids = Column(JSONB, default=list)

    # Metadata
    message_count = Column(Integer)
    tool_call_count = Column(Integer)
    duration_seconds = Column(Integer)

    # Vector embedding for semantic search
    embedding = Column(Vector(768))

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    session = relationship("AgentSession", backref="summary")

    __table_args__ = (
        Index("idx_session_summaries_embedding", embedding, postgresql_using="ivfflat",
              postgresql_with={"lists": 100}, postgresql_ops={"embedding": "vector_cosine_ops"}),
    )
```

### Step 2: Update models.py

Add to end of `shared/netagent_core/db/models.py`:

```python
# Memory models
from .models.memory import Memory, SessionSummary
```

### Step 3: Update __init__.py exports

Add to `shared/netagent_core/db/__init__.py`:

```python
from .models import Memory, SessionSummary
```

### Step 4: Commit

```bash
git add shared/netagent_core/db/
git commit -m "feat(db): add Memory and SessionSummary models"
```

---

## Task 8.2: Create Memory Service

**Files:**
- Create: `shared/netagent_core/memory/__init__.py`
- Create: `shared/netagent_core/memory/service.py`

### Step 1: Create memory service

Create `shared/netagent_core/memory/__init__.py`:

```python
"""Memory service module."""

from .service import MemoryService

__all__ = ["MemoryService"]
```

Create `shared/netagent_core/memory/service.py`:

```python
"""Memory service for storing and retrieving agent memories."""

import logging
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from netagent_core.db import Memory, SessionSummary, AgentSession

logger = logging.getLogger(__name__)


class MemoryService:
    """Service for managing agent memories."""

    def __init__(self, db: Session, embedding_client=None):
        self.db = db
        self.embedding_client = embedding_client

    def store_memory(
        self,
        content: str,
        memory_type: str,
        user_id: Optional[int] = None,
        agent_id: Optional[int] = None,
        category: Optional[str] = None,
        tags: Optional[list] = None,
        source_session_id: Optional[int] = None,
        source_job_id: Optional[int] = None,
        confidence: float = 1.0,
        created_by: Optional[int] = None,
    ) -> Memory:
        """Store a new memory.

        Args:
            content: The memory content
            memory_type: Type of memory (preference, fact, summary, instruction)
            user_id: Scope to specific user (None for non-user-specific)
            agent_id: Scope to specific agent (None for non-agent-specific)
            category: Category for organization (e.g., "device_info")
            tags: Searchable tags
            source_session_id: Session where this was learned
            source_job_id: Job where this was learned
            confidence: Confidence score 0-1
            created_by: User who created this memory

        Returns:
            Created Memory object
        """
        # Check for duplicate/similar memory
        existing = self._find_similar_memory(content, user_id, agent_id)
        if existing:
            # Update existing instead of creating duplicate
            existing.content = content
            existing.confidence = max(existing.confidence, confidence)
            existing.updated_at = datetime.utcnow()
            existing.access_count += 1
            self.db.commit()
            logger.info(f"Updated existing memory {existing.id}")
            return existing

        # Generate embedding if client available
        embedding = None
        if self.embedding_client:
            try:
                embedding = self.embedding_client.embed(content)
            except Exception as e:
                logger.warning(f"Failed to generate embedding: {e}")

        memory = Memory(
            content=content,
            memory_type=memory_type,
            user_id=user_id,
            agent_id=agent_id,
            category=category,
            tags=tags or [],
            source_session_id=source_session_id,
            source_job_id=source_job_id,
            confidence=confidence,
            embedding=embedding,
            created_by=created_by,
        )

        self.db.add(memory)
        self.db.commit()

        logger.info(f"Stored memory {memory.id}: {content[:50]}...")
        return memory

    def recall_memories(
        self,
        query: str,
        user_id: Optional[int] = None,
        agent_id: Optional[int] = None,
        memory_type: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 10,
        include_global: bool = True,
    ) -> list[Memory]:
        """Recall relevant memories.

        Retrieves memories that match the query, scoped appropriately.

        Args:
            query: Search query (semantic search if embeddings available)
            user_id: Include user-specific memories
            agent_id: Include agent-specific memories
            memory_type: Filter by type
            category: Filter by category
            limit: Maximum memories to return
            include_global: Include global (unscoped) memories

        Returns:
            List of relevant Memory objects
        """
        # Build scope filter
        scope_conditions = []

        if include_global:
            # Global memories (no user_id, no agent_id)
            scope_conditions.append(
                and_(Memory.user_id.is_(None), Memory.agent_id.is_(None))
            )

        if user_id:
            scope_conditions.append(Memory.user_id == user_id)

        if agent_id:
            scope_conditions.append(Memory.agent_id == agent_id)

        base_query = self.db.query(Memory).filter(
            Memory.is_active == True,
            or_(*scope_conditions) if scope_conditions else True,
        )

        if memory_type:
            base_query = base_query.filter(Memory.memory_type == memory_type)

        if category:
            base_query = base_query.filter(Memory.category == category)

        # Check for expiration
        base_query = base_query.filter(
            or_(Memory.expires_at.is_(None), Memory.expires_at > datetime.utcnow())
        )

        # Try semantic search if we have embedding client
        if self.embedding_client:
            try:
                query_embedding = self.embedding_client.embed(query)
                # Use pgvector cosine distance
                memories = base_query.order_by(
                    Memory.embedding.cosine_distance(query_embedding)
                ).limit(limit).all()
            except Exception as e:
                logger.warning(f"Semantic search failed, falling back to text: {e}")
                memories = self._text_search(base_query, query, limit)
        else:
            memories = self._text_search(base_query, query, limit)

        # Update access tracking
        for memory in memories:
            memory.access_count += 1
            memory.last_accessed_at = datetime.utcnow()
        self.db.commit()

        return memories

    def _text_search(self, base_query, query: str, limit: int) -> list[Memory]:
        """Fallback text-based search."""
        query_lower = query.lower()
        keywords = query_lower.split()

        # Simple keyword matching
        memories = base_query.filter(
            or_(*[Memory.content.ilike(f"%{kw}%") for kw in keywords])
        ).order_by(Memory.confidence.desc(), Memory.access_count.desc()).limit(limit).all()

        return memories

    def _find_similar_memory(
        self,
        content: str,
        user_id: Optional[int],
        agent_id: Optional[int],
    ) -> Optional[Memory]:
        """Find existing similar memory to avoid duplicates."""
        # Simple check - exact or very similar content
        content_lower = content.lower().strip()

        query = self.db.query(Memory).filter(
            Memory.is_active == True,
            Memory.user_id == user_id,
            Memory.agent_id == agent_id,
        )

        for memory in query.all():
            if memory.content.lower().strip() == content_lower:
                return memory
            # Could add fuzzy matching here

        return None

    def forget_memory(self, memory_id: int) -> bool:
        """Soft-delete a memory."""
        memory = self.db.query(Memory).filter(Memory.id == memory_id).first()
        if memory:
            memory.is_active = False
            self.db.commit()
            logger.info(f"Forgot memory {memory_id}")
            return True
        return False

    def get_user_preferences(self, user_id: int) -> list[Memory]:
        """Get all preferences for a user."""
        return self.db.query(Memory).filter(
            Memory.user_id == user_id,
            Memory.memory_type == "preference",
            Memory.is_active == True,
        ).order_by(Memory.updated_at.desc()).all()

    def summarize_session(
        self,
        session_id: int,
        llm_client=None,
    ) -> Optional[SessionSummary]:
        """Generate a summary for a completed session.

        Args:
            session_id: Session to summarize
            llm_client: LLM client for generating summary

        Returns:
            Created SessionSummary or None
        """
        session = self.db.query(AgentSession).filter(
            AgentSession.id == session_id
        ).first()

        if not session:
            return None

        if session.status not in ["completed", "failed"]:
            logger.warning(f"Session {session_id} not complete, skipping summary")
            return None

        # Check if summary already exists
        existing = self.db.query(SessionSummary).filter(
            SessionSummary.session_id == session_id
        ).first()
        if existing:
            return existing

        # Build summary from session data
        messages = session.messages
        actions = session.actions

        # Calculate stats
        message_count = len(messages)
        tool_call_count = len([a for a in actions if a.tool_name])
        tools_used = list(set([a.tool_name for a in actions if a.tool_name]))

        duration = None
        if session.completed_at and session.created_at:
            duration = int((session.completed_at - session.created_at).total_seconds())

        # Generate natural language summary
        if llm_client:
            summary_text = self._generate_summary_with_llm(session, llm_client)
            key_findings = self._extract_findings_with_llm(session, llm_client)
        else:
            summary_text = self._generate_basic_summary(session)
            key_findings = []

        key_actions = [
            {"tool": a.tool_name, "status": a.status}
            for a in actions if a.tool_name
        ][:10]  # Top 10

        # Generate embedding
        embedding = None
        if self.embedding_client:
            try:
                embedding = self.embedding_client.embed(summary_text)
            except Exception as e:
                logger.warning(f"Failed to generate summary embedding: {e}")

        summary = SessionSummary(
            session_id=session_id,
            summary=summary_text,
            key_actions=key_actions,
            key_findings=key_findings,
            tools_used=tools_used,
            message_count=message_count,
            tool_call_count=tool_call_count,
            duration_seconds=duration,
            embedding=embedding,
        )

        self.db.add(summary)
        self.db.commit()

        logger.info(f"Created summary for session {session_id}")
        return summary

    def _generate_basic_summary(self, session: AgentSession) -> str:
        """Generate basic summary without LLM."""
        agent_name = session.agent.name if session.agent else "Unknown Agent"
        message_count = len(session.messages)
        tool_count = len([a for a in session.actions if a.tool_name])

        status = "completed successfully" if session.status == "completed" else f"ended with status: {session.status}"

        return f"Session with {agent_name} {status}. Exchanged {message_count} messages and made {tool_count} tool calls."

    def _generate_summary_with_llm(self, session: AgentSession, llm_client) -> str:
        """Generate rich summary using LLM."""
        # Build context from session
        messages_text = "\n".join([
            f"{m.role}: {m.content[:200]}"
            for m in session.messages[-20:]  # Last 20 messages
        ])

        prompt = f"""Summarize this agent session in 2-3 sentences. Focus on:
- What was the user trying to accomplish?
- What did the agent do?
- What was the outcome?

Session messages:
{messages_text}

Summary:"""

        try:
            response = llm_client.generate(prompt, max_tokens=200)
            return response.strip()
        except Exception as e:
            logger.error(f"LLM summary generation failed: {e}")
            return self._generate_basic_summary(session)

    def _extract_findings_with_llm(self, session: AgentSession, llm_client) -> list:
        """Extract key findings using LLM."""
        # This would use the LLM to identify facts worth remembering
        # For now, return empty list
        return []
```

### Step 2: Commit

```bash
git add shared/netagent_core/memory/
git commit -m "feat(memory): add MemoryService for persistent agent memory"
```

---

## Task 8.3: Create Memory Tool

**Files:**
- Create: `shared/netagent_core/tools/memory_tool.py`
- Modify: `shared/netagent_core/tools/__init__.py`

### Step 1: Create memory tool

Create `shared/netagent_core/tools/memory_tool.py`:

```python
"""Memory tool for agents to store and recall information."""

import logging
from typing import Optional

from .base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class RecallMemoryTool(BaseTool):
    """Tool for recalling relevant memories."""

    name = "recall_memory"
    description = """Recall relevant memories and context from previous sessions.

Use this to:
- Check user preferences before taking action
- Recall facts about devices, networks, or configurations
- Find relevant information from past interactions

The query should describe what you're looking for."""

    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for in memory (e.g., 'user preferences for output format', 'information about router-01')"
            },
            "category": {
                "type": "string",
                "description": "Optional category filter (e.g., 'device_info', 'user_preference')",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum memories to return (default 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    requires_approval = False
    risk_level = "low"

    def __init__(self, memory_service, user_id: Optional[int] = None, agent_id: Optional[int] = None):
        self.memory_service = memory_service
        self.user_id = user_id
        self.agent_id = agent_id

    async def execute(self, query: str, category: Optional[str] = None, limit: int = 5) -> ToolResult:
        """Execute memory recall."""
        try:
            memories = self.memory_service.recall_memories(
                query=query,
                user_id=self.user_id,
                agent_id=self.agent_id,
                category=category,
                limit=limit,
                include_global=True,
            )

            if not memories:
                return ToolResult(
                    success=True,
                    output="No relevant memories found.",
                    metadata={"count": 0}
                )

            # Format memories for agent
            formatted = []
            for m in memories:
                scope = []
                if m.user_id:
                    scope.append("user-specific")
                if m.agent_id:
                    scope.append("agent-specific")
                if not scope:
                    scope.append("global")

                formatted.append({
                    "content": m.content,
                    "type": m.memory_type,
                    "category": m.category,
                    "scope": ", ".join(scope),
                    "confidence": m.confidence,
                })

            return ToolResult(
                success=True,
                output=f"Found {len(memories)} relevant memories:\n\n" +
                       "\n\n".join([f"- [{m['type']}] {m['content']} (confidence: {m['confidence']:.0%})" for m in formatted]),
                metadata={"count": len(memories), "memories": formatted}
            )

        except Exception as e:
            logger.error(f"Memory recall failed: {e}")
            return ToolResult(
                success=False,
                output=f"Failed to recall memories: {str(e)}",
                error=str(e)
            )


class StoreMemoryTool(BaseTool):
    """Tool for storing new memories."""

    name = "store_memory"
    description = """Store important information for future reference.

Use this to remember:
- User preferences (e.g., "User prefers CSV format for reports")
- Facts about devices or networks (e.g., "router-01 runs Junos 21.4R3")
- Important findings from tasks
- Instructions for future interactions

Be selective - only store information that will be useful later."""

    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The information to remember"
            },
            "memory_type": {
                "type": "string",
                "enum": ["preference", "fact", "instruction"],
                "description": "Type of memory: preference (user likes/dislikes), fact (objective info), instruction (how to do something)"
            },
            "category": {
                "type": "string",
                "description": "Category for organization (e.g., 'device_info', 'output_format', 'network_topology')",
            },
            "scope": {
                "type": "string",
                "enum": ["user", "agent", "global"],
                "description": "Who this memory applies to: user (current user only), agent (this agent), global (everyone)",
                "default": "user"
            },
        },
        "required": ["content", "memory_type"],
    }

    requires_approval = False
    risk_level = "low"

    def __init__(self, memory_service, user_id: Optional[int] = None, agent_id: Optional[int] = None, session_id: Optional[int] = None):
        self.memory_service = memory_service
        self.user_id = user_id
        self.agent_id = agent_id
        self.session_id = session_id

    async def execute(
        self,
        content: str,
        memory_type: str,
        category: Optional[str] = None,
        scope: str = "user",
    ) -> ToolResult:
        """Execute memory storage."""
        try:
            # Determine scoping
            user_id = self.user_id if scope in ["user"] else None
            agent_id = self.agent_id if scope in ["agent"] else None

            memory = self.memory_service.store_memory(
                content=content,
                memory_type=memory_type,
                user_id=user_id,
                agent_id=agent_id,
                category=category,
                source_session_id=self.session_id,
                created_by=self.user_id,
            )

            scope_desc = f"for {'this user' if user_id else 'this agent' if agent_id else 'everyone'}"

            return ToolResult(
                success=True,
                output=f"Stored memory {scope_desc}: {content[:100]}{'...' if len(content) > 100 else ''}",
                metadata={"memory_id": memory.id, "scope": scope}
            )

        except Exception as e:
            logger.error(f"Memory storage failed: {e}")
            return ToolResult(
                success=False,
                output=f"Failed to store memory: {str(e)}",
                error=str(e)
            )
```

### Step 2: Update tools __init__.py

Add to `shared/netagent_core/tools/__init__.py`:

```python
from .memory_tool import RecallMemoryTool, StoreMemoryTool
```

### Step 3: Commit

```bash
git add shared/netagent_core/tools/
git commit -m "feat(tools): add recall_memory and store_memory tools"
```

---

## Task 8.4: Integrate Memory into Agent Executor

**Files:**
- Modify: `services/worker/app/tasks/agent_executor.py`

### Step 1: Update build_tools_for_agent

Add to `build_tools_for_agent` function in `agent_executor.py`:

```python
from netagent_core.memory import MemoryService
from netagent_core.tools import RecallMemoryTool, StoreMemoryTool

def build_tools_for_agent(agent: Agent, db_session_factory, session_id: int):
    """Build tools for agent execution."""
    tools = []

    # ... existing tool building code ...

    # Add memory tools (always available)
    with db_session_factory() as db:
        session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
        user_id = session.user_id if session else None

        memory_service = MemoryService(db)

        tools.append(RecallMemoryTool(
            memory_service=memory_service,
            user_id=user_id,
            agent_id=agent.id,
        ))

        tools.append(StoreMemoryTool(
            memory_service=memory_service,
            user_id=user_id,
            agent_id=agent.id,
            session_id=session_id,
        ))

    return tools
```

### Step 2: Add session summary on completion

Add to end of `execute_agent_session` task:

```python
@shared_task
def execute_agent_session(session_id: int, initial_message: str = None):
    # ... existing code ...

    # On successful completion, generate summary
    if session.status == "completed":
        try:
            from netagent_core.memory import MemoryService

            with get_db_context() as db:
                memory_service = MemoryService(db)
                memory_service.summarize_session(session_id)
        except Exception as e:
            logger.warning(f"Failed to generate session summary: {e}")
```

### Step 3: Commit

```bash
git add services/worker/app/tasks/agent_executor.py
git commit -m "feat(worker): integrate memory tools into agent executor"
```

---

## Task 8.5: Add Memory API Endpoints

**Files:**
- Create: `services/api/app/routes/memory.py`
- Modify: `services/api/app/main.py`

### Step 1: Create memory routes

Create `services/api/app/routes/memory.py`:

```python
"""Memory management API routes."""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, Memory, User
from netagent_core.memory import MemoryService
from ..deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryCreate(BaseModel):
    content: str
    memory_type: str
    category: Optional[str] = None
    scope: str = "user"  # user, agent, global


class MemoryResponse(BaseModel):
    id: int
    content: str
    memory_type: str
    category: Optional[str]
    confidence: float
    is_user_specific: bool
    is_agent_specific: bool
    access_count: int

    class Config:
        from_attributes = True


@router.get("/recall")
def recall_memories(
    query: str = Query(..., description="Search query"),
    category: Optional[str] = Query(None),
    limit: int = Query(10, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Recall relevant memories for current user."""
    service = MemoryService(db)

    memories = service.recall_memories(
        query=query,
        user_id=current_user.id,
        category=category,
        limit=limit,
    )

    return [
        {
            "id": m.id,
            "content": m.content,
            "memory_type": m.memory_type,
            "category": m.category,
            "confidence": m.confidence,
            "is_user_specific": m.user_id is not None,
            "is_agent_specific": m.agent_id is not None,
        }
        for m in memories
    ]


@router.post("/")
def create_memory(
    request: MemoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually create a memory."""
    service = MemoryService(db)

    user_id = current_user.id if request.scope == "user" else None

    memory = service.store_memory(
        content=request.content,
        memory_type=request.memory_type,
        category=request.category,
        user_id=user_id,
        created_by=current_user.id,
    )

    return {"id": memory.id, "content": memory.content}


@router.get("/preferences")
def get_my_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get current user's stored preferences."""
    service = MemoryService(db)
    preferences = service.get_user_preferences(current_user.id)

    return [
        {
            "id": p.id,
            "content": p.content,
            "category": p.category,
        }
        for p in preferences
    ]


@router.delete("/{memory_id}")
def forget_memory(
    memory_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a memory."""
    memory = db.query(Memory).filter(Memory.id == memory_id).first()

    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    # Only allow deleting own memories or if admin
    if memory.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    service = MemoryService(db)
    service.forget_memory(memory_id)

    return {"status": "forgotten"}
```

### Step 2: Register router

Add to `services/api/app/main.py`:

```python
from .routes.memory import router as memory_router

app.include_router(memory_router, prefix="/api")
```

### Step 3: Commit

```bash
git add services/api/app/routes/memory.py services/api/app/main.py
git commit -m "feat(api): add memory management endpoints"
```

---

## Task 8.6: Update Agent System Prompts

Agents should be instructed to use memory. Update default system prompt template:

```markdown
## Memory

You have access to persistent memory. Use it wisely:

**Before taking action:**
- Use `recall_memory` to check for user preferences or relevant past information
- Example: Before formatting output, check "user preferences for output format"

**After learning something important:**
- Use `store_memory` to remember facts, preferences, or instructions
- Only store information that will be useful in future sessions
- Be concise - store the key fact, not the entire context

Memory types:
- `preference`: User likes/dislikes (e.g., "User prefers tables over bullet lists")
- `fact`: Objective information (e.g., "router-01 IP is 192.168.1.1")
- `instruction`: How to do something (e.g., "Always check BGP before making changes")
```

---

## Summary

Phase 8 adds:

1. **Memory model** - Stores facts, preferences, instructions with scoping
2. **SessionSummary model** - Auto-generated summaries of completed sessions
3. **MemoryService** - Store, recall, and manage memories with semantic search
4. **Memory tools** - `recall_memory` and `store_memory` for agents
5. **Memory API** - Endpoints for users to view/manage their memories
6. **Integration** - Memory tools available to all agents, summaries auto-generated

This enables agents to:
- Remember user preferences across sessions
- Accumulate knowledge about the network
- Learn from past interactions
- Provide more personalized assistance

---

## Verification

### 1. Database Verification

```bash
psql $DATABASE_URL -c "\d memories"
psql $DATABASE_URL -c "\d session_summaries"
```

### 2. Test Memory Service

```bash
python3 -c "
from netagent_core.db import get_db_context
from netagent_core.memory import MemoryService

with get_db_context() as db:
    service = MemoryService(db)

    # Store memory
    mem = service.store_memory(
        content='User prefers JSON output format',
        memory_type='preference',
        category='output_format',
        user_id=1,
    )
    print(f'✓ Stored memory {mem.id}')

    # Recall memory
    results = service.recall_memories('output format', user_id=1)
    assert len(results) > 0
    print(f'✓ Recalled {len(results)} memories')

    # Cleanup
    service.forget_memory(mem.id)
    print('✓ Memory forgotten')
"
```

### 3. Test Memory Tools

```bash
python3 -c "
from netagent_core.db import get_db_context
from netagent_core.memory import MemoryService
from netagent_core.tools import RecallMemoryTool, StoreMemoryTool
import asyncio

async def test():
    with get_db_context() as db:
        service = MemoryService(db)

        store_tool = StoreMemoryTool(service, user_id=1, agent_id=1)
        result = await store_tool.execute(
            content='Test device router-01 has IP 10.0.0.1',
            memory_type='fact',
            category='device_info',
        )
        assert result.success
        print(f'✓ Store tool: {result.output}')

        recall_tool = RecallMemoryTool(service, user_id=1, agent_id=1)
        result = await recall_tool.execute(query='router-01')
        assert result.success
        print(f'✓ Recall tool: {result.output[:100]}...')

asyncio.run(test())
"
```

### 4. API Verification

```bash
TOKEN="your-auth-token"
API_URL="http://localhost:8000"

# Create memory
curl -s -X POST "$API_URL/api/memory/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"content": "User prefers tables", "memory_type": "preference", "category": "output"}' | jq .

# Recall memories
curl -s "$API_URL/api/memory/recall?query=tables" \
  -H "Authorization: Bearer $TOKEN" | jq .

# Get preferences
curl -s "$API_URL/api/memory/preferences" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

### 5. Playwright UI Test (if memory UI added)

```python
# tests/e2e/test_memory_ui.py
from playwright.sync_api import Page, expect

def test_preferences_page(authenticated_page: Page):
    """User can view their memory preferences."""
    authenticated_page.goto("http://localhost:8089/settings/memory")
    expect(authenticated_page.locator("h2")).to_contain_text("Memory")
```

### Expected Outcomes

- [ ] `memories` and `session_summaries` tables exist
- [ ] Memory storage works with deduplication
- [ ] Memory recall returns relevant results
- [ ] Memory tools execute successfully
- [ ] API endpoints respond correctly
- [ ] Memories scoped by user/agent/global
