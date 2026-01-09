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

from ..database import Base

# Try to import pgvector, but make it optional
try:
    from pgvector.sqlalchemy import Vector
    HAS_PGVECTOR = True
except ImportError:
    HAS_PGVECTOR = False
    Vector = None


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

    # Vector embedding for semantic search (optional, requires pgvector)
    embedding = Column(JSONB) if not HAS_PGVECTOR else Column(Vector(768))

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
    )

    def __repr__(self):
        return f"<Memory {self.id}: {self.memory_type} - {self.content[:50]}...>"


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

    # Vector embedding for semantic search (optional, requires pgvector)
    embedding = Column(JSONB) if not HAS_PGVECTOR else Column(Vector(768))

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    session = relationship("AgentSession", backref="summary")

    def __repr__(self):
        return f"<SessionSummary {self.id} for session {self.session_id}>"
