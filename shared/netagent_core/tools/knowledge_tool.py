"""Knowledge base search tool for RAG.

Provides vector similarity search across knowledge bases using pgvector.
"""

import logging
from typing import List, Optional, Dict, Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..llm.agent_executor import ToolDefinition

logger = logging.getLogger(__name__)


class KnowledgeSearchTool:
    """Tool for searching knowledge bases using vector similarity."""

    name = "search_knowledge"
    description = """Search the knowledge base for relevant documentation and information.
Use this tool to find answers from internal documentation, runbooks, and wiki pages."""

    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query - describe what information you're looking for",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results to return (default: 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    requires_approval = False
    risk_level = "low"

    def __init__(
        self,
        knowledge_base_ids: List[int] = None,
        db_session_factory=None,
        embeddings_client=None,
    ):
        """Initialize knowledge search tool.

        Args:
            knowledge_base_ids: IDs of knowledge bases to search
            db_session_factory: Factory for database sessions
            embeddings_client: Client for generating embeddings
        """
        self.knowledge_base_ids = knowledge_base_ids or []
        self.db_session_factory = db_session_factory
        self.embeddings_client = embeddings_client

    async def execute(self, query: str, top_k: int = 5) -> str:
        """Search knowledge bases for relevant content.

        Args:
            query: Search query
            top_k: Number of results to return

        Returns:
            Formatted search results
        """
        if not self.knowledge_base_ids:
            return "No knowledge bases configured for this agent"

        if not self.db_session_factory:
            return "Knowledge base search not available (no database connection)"

        try:
            # Generate query embedding
            query_embedding = await self._get_embedding(query)
            if not query_embedding:
                # Fall back to text search if embeddings not available
                return await self._text_search(query, top_k)

            # Vector similarity search
            results = await self._vector_search(query_embedding, top_k)

            if not results:
                return f"No relevant documents found for: {query}"

            # Format results
            output = f"Found {len(results)} relevant documents:\n\n"
            for i, result in enumerate(results, 1):
                output += f"--- Result {i} (relevance: {result['similarity']:.2%}) ---\n"
                output += f"Source: {result['title']}\n"
                if result.get('url'):
                    output += f"URL: {result['url']}\n"
                output += f"\n{result['content']}\n\n"

            return output

        except Exception as e:
            logger.error(f"Knowledge search error: {e}")
            return f"Error searching knowledge base: {str(e)}"

    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Get embedding vector for text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector or None
        """
        if self.embeddings_client:
            try:
                return await self.embeddings_client.embed_query(text)
            except Exception as e:
                logger.error(f"Embedding error: {e}")
                return None

        # Try to use GeminiEmbeddings from knowledge module
        try:
            from ..knowledge import GeminiEmbeddings

            embeddings = GeminiEmbeddings()
            return await embeddings.embed_query(text)

        except Exception as e:
            logger.warning(f"Could not generate embedding: {e}")
            return None

    async def _vector_search(
        self,
        query_embedding: List[float],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """Search using vector similarity.

        Args:
            query_embedding: Query embedding vector
            top_k: Number of results

        Returns:
            List of search results
        """
        try:
            with self.db_session_factory() as db:
                # Build query with pgvector cosine similarity
                # Use CAST instead of :: to avoid SQLAlchemy parameter parsing issues
                sql = text("""
                    SELECT
                        kc.content,
                        kc.chunk_metadata,
                        kd.title,
                        kd.source_url as url,
                        1 - (kc.embedding <=> CAST(:query_embedding AS vector)) as similarity
                    FROM knowledge_chunks kc
                    JOIN knowledge_documents kd ON kc.document_id = kd.id
                    WHERE kd.knowledge_base_id = ANY(:kb_ids)
                    ORDER BY kc.embedding <=> CAST(:query_embedding AS vector)
                    LIMIT :top_k
                """)

                result = db.execute(sql, {
                    "query_embedding": str(query_embedding),
                    "kb_ids": self.knowledge_base_ids,
                    "top_k": top_k,
                })

                rows = result.fetchall()
                return [
                    {
                        "content": row.content,
                        "metadata": row.chunk_metadata,
                        "title": row.title,
                        "url": row.url,
                        "similarity": row.similarity,
                    }
                    for row in rows
                ]

        except Exception as e:
            logger.error(f"Vector search error: {e}")
            return []

    async def _text_search(self, query: str, top_k: int) -> str:
        """Fall back to text-based search.

        Args:
            query: Search query
            top_k: Number of results

        Returns:
            Formatted search results
        """
        try:
            with self.db_session_factory() as db:
                # Simple ILIKE search
                sql = text("""
                    SELECT
                        kc.content,
                        kd.title,
                        kd.source_url as url
                    FROM knowledge_chunks kc
                    JOIN knowledge_documents kd ON kc.document_id = kd.id
                    WHERE kd.knowledge_base_id = ANY(:kb_ids)
                    AND (
                        kc.content ILIKE :search_pattern
                        OR kd.title ILIKE :search_pattern
                    )
                    LIMIT :top_k
                """)

                result = db.execute(sql, {
                    "kb_ids": self.knowledge_base_ids,
                    "search_pattern": f"%{query}%",
                    "top_k": top_k,
                })

                rows = result.fetchall()

                if not rows:
                    return f"No documents found matching: {query}"

                output = f"Found {len(rows)} matching documents:\n\n"
                for i, row in enumerate(rows, 1):
                    output += f"--- Result {i} ---\n"
                    output += f"Source: {row.title}\n"
                    if row.url:
                        output += f"URL: {row.url}\n"
                    output += f"\n{row.content[:500]}...\n\n"

                return output

        except Exception as e:
            logger.error(f"Text search error: {e}")
            return f"Error searching: {str(e)}"


def create_knowledge_search_tool(
    knowledge_base_ids: List[int] = None,
    db_session_factory=None,
    embeddings_client=None,
) -> ToolDefinition:
    """Create knowledge search tool definition for agent executor.

    Args:
        knowledge_base_ids: Knowledge bases to search
        db_session_factory: Database session factory
        embeddings_client: Embeddings client

    Returns:
        ToolDefinition for the knowledge search tool
    """
    tool = KnowledgeSearchTool(
        knowledge_base_ids=knowledge_base_ids,
        db_session_factory=db_session_factory,
        embeddings_client=embeddings_client,
    )

    return ToolDefinition(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters,
        handler=tool.execute,
        requires_approval=tool.requires_approval,
        risk_level=tool.risk_level,
    )
