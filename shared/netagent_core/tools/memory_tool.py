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
