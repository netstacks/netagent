"""Memory service for storing and retrieving agent memories."""

import logging
from datetime import datetime
from typing import Optional, List, Any
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from netagent_core.db import Memory, SessionSummary, AgentSession

logger = logging.getLogger(__name__)


class MemoryService:
    """Service for managing agent memories.

    Provides methods to store, recall, and manage persistent memories
    for agents. Memories can be scoped to users, agents, or be global.
    """

    def __init__(self, db: Session, embedding_client=None):
        """Initialize the memory service.

        Args:
            db: SQLAlchemy database session
            embedding_client: Optional client for generating embeddings
        """
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
    ) -> List[Memory]:
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

        # Try semantic search if we have embedding client and vector support
        memories = self._text_search(base_query, query, limit)

        # Update access tracking
        for memory in memories:
            memory.access_count += 1
            memory.last_accessed_at = datetime.utcnow()
        self.db.commit()

        return memories

    def _text_search(self, base_query, query: str, limit: int) -> List[Memory]:
        """Fallback text-based search."""
        query_lower = query.lower()
        keywords = query_lower.split()

        # Simple keyword matching
        if keywords:
            memories = base_query.filter(
                or_(*[Memory.content.ilike(f"%{kw}%") for kw in keywords])
            ).order_by(Memory.confidence.desc(), Memory.access_count.desc()).limit(limit).all()
        else:
            memories = base_query.order_by(
                Memory.confidence.desc(), Memory.access_count.desc()
            ).limit(limit).all()

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

    def get_user_preferences(self, user_id: int) -> List[Memory]:
        """Get all preferences for a user."""
        return self.db.query(Memory).filter(
            Memory.user_id == user_id,
            Memory.memory_type == "preference",
            Memory.is_active == True,
        ).order_by(Memory.updated_at.desc()).all()

    def get_agent_knowledge(self, agent_id: int, category: Optional[str] = None) -> List[Memory]:
        """Get all knowledge for an agent."""
        query = self.db.query(Memory).filter(
            Memory.agent_id == agent_id,
            Memory.memory_type == "fact",
            Memory.is_active == True,
        )

        if category:
            query = query.filter(Memory.category == category)

        return query.order_by(Memory.confidence.desc()).all()

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
        messages = session.messages if hasattr(session, 'messages') else []
        actions = session.actions if hasattr(session, 'actions') else []

        # Calculate stats
        message_count = len(messages) if messages else 0
        tool_call_count = len([a for a in actions if hasattr(a, 'tool_name') and a.tool_name]) if actions else 0
        tools_used = list(set([a.tool_name for a in actions if hasattr(a, 'tool_name') and a.tool_name])) if actions else []

        duration = None
        if hasattr(session, 'completed_at') and session.completed_at and hasattr(session, 'created_at') and session.created_at:
            duration = int((session.completed_at - session.created_at).total_seconds())

        # Generate natural language summary
        if llm_client:
            summary_text = self._generate_summary_with_llm(session, llm_client)
            key_findings = self._extract_findings_with_llm(session, llm_client)
        else:
            summary_text = self._generate_basic_summary(session)
            key_findings = []

        key_actions = [
            {"tool": a.tool_name, "status": getattr(a, 'status', 'unknown')}
            for a in actions if hasattr(a, 'tool_name') and a.tool_name
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
        agent_name = session.agent.name if hasattr(session, 'agent') and session.agent else "Unknown Agent"
        messages = session.messages if hasattr(session, 'messages') else []
        actions = session.actions if hasattr(session, 'actions') else []
        message_count = len(messages) if messages else 0
        tool_count = len([a for a in actions if hasattr(a, 'tool_name') and a.tool_name]) if actions else 0

        status = "completed successfully" if session.status == "completed" else f"ended with status: {session.status}"

        return f"Session with {agent_name} {status}. Exchanged {message_count} messages and made {tool_count} tool calls."

    def _generate_summary_with_llm(self, session: AgentSession, llm_client) -> str:
        """Generate rich summary using LLM."""
        # Build context from session
        messages = session.messages if hasattr(session, 'messages') else []
        messages_text = "\n".join([
            f"{m.role}: {m.content[:200]}"
            for m in messages[-20:]  # Last 20 messages
        ]) if messages else "No messages"

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

    def get_context_for_session(
        self,
        user_id: Optional[int],
        agent_id: Optional[int],
        topic_hint: Optional[str] = None,
        limit: int = 5,
    ) -> str:
        """Get formatted context from memories for a new session.

        Args:
            user_id: User starting the session
            agent_id: Agent being used
            topic_hint: Optional hint about the session topic
            limit: Maximum memories to include

        Returns:
            Formatted string of relevant memories
        """
        query = topic_hint or "relevant context"
        memories = self.recall_memories(
            query=query,
            user_id=user_id,
            agent_id=agent_id,
            limit=limit,
            include_global=True,
        )

        if not memories:
            return ""

        context_parts = ["## Relevant Context from Memory\n"]

        # Group by type
        preferences = [m for m in memories if m.memory_type == "preference"]
        facts = [m for m in memories if m.memory_type == "fact"]
        instructions = [m for m in memories if m.memory_type == "instruction"]

        if preferences:
            context_parts.append("**User Preferences:**")
            for m in preferences:
                context_parts.append(f"- {m.content}")
            context_parts.append("")

        if facts:
            context_parts.append("**Known Facts:**")
            for m in facts:
                context_parts.append(f"- {m.content}")
            context_parts.append("")

        if instructions:
            context_parts.append("**Instructions:**")
            for m in instructions:
                context_parts.append(f"- {m.content}")
            context_parts.append("")

        return "\n".join(context_parts)
