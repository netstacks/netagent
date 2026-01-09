"""Knowledge base indexer for syncing and embedding documents."""

import hashlib
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import KnowledgeBase, KnowledgeDocument, KnowledgeChunk
from .confluence_client import ConfluenceClient, ConfluencePage
from .chunker import TextChunker
from .embeddings import EmbeddingsClient

logger = logging.getLogger(__name__)


class KnowledgeIndexer:
    """Index documents from various sources into vector store.

    Usage:
        indexer = KnowledgeIndexer(db_session)
        await indexer.sync_knowledge_base(kb_id)
    """

    def __init__(
        self,
        db: Session,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ):
        """Initialize indexer.

        Args:
            db: SQLAlchemy database session
            chunk_size: Target chunk size in characters
            chunk_overlap: Overlap between chunks
        """
        self.db = db
        self.chunker = TextChunker(chunk_size=chunk_size, overlap=chunk_overlap)
        self.embeddings = EmbeddingsClient()

    async def sync_knowledge_base(self, kb_id: int) -> dict:
        """Sync a knowledge base from its source.

        Args:
            kb_id: Knowledge base ID

        Returns:
            Dict with sync statistics
        """
        kb = self.db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        if not kb:
            raise ValueError(f"Knowledge base {kb_id} not found")

        logger.info(f"Starting sync for knowledge base: {kb.name}")
        kb.sync_status = "syncing"
        self.db.commit()

        stats = {
            "documents_added": 0,
            "documents_updated": 0,
            "documents_unchanged": 0,
            "chunks_created": 0,
            "errors": [],
        }

        try:
            if kb.source_type == "confluence":
                await self._sync_confluence(kb, stats)
            elif kb.source_type == "manual":
                # Manual documents are added via API, no sync needed
                pass
            else:
                raise ValueError(f"Unsupported source type: {kb.source_type}")

            kb.sync_status = "completed"
            kb.last_sync_at = datetime.utcnow()

            # Update counts
            kb.document_count = self.db.query(KnowledgeDocument).filter(
                KnowledgeDocument.knowledge_base_id == kb_id
            ).count()
            kb.chunk_count = self.db.query(KnowledgeChunk).join(KnowledgeDocument).filter(
                KnowledgeDocument.knowledge_base_id == kb_id
            ).count()

            self.db.commit()
            logger.info(f"Sync completed for {kb.name}: {stats}")

        except Exception as e:
            logger.error(f"Sync failed for {kb.name}: {e}")
            kb.sync_status = "failed"
            self.db.commit()
            stats["errors"].append(str(e))

        return stats

    async def _sync_confluence(self, kb: KnowledgeBase, stats: dict):
        """Sync from Confluence source."""
        config = kb.source_config or {}

        client = ConfluenceClient(
            base_url=config.get("base_url"),
            username=config.get("username"),
            api_token=config.get("api_token"),
        )

        pages: List[ConfluencePage] = []

        if config.get("parent_page_id"):
            # Sync page tree
            pages = await client.get_page_tree(config["parent_page_id"])
        elif config.get("space_key"):
            # Sync entire space
            pages = await client.get_space_pages(config["space_key"])
        else:
            raise ValueError("Confluence config must have parent_page_id or space_key")

        for page in pages:
            try:
                await self._index_page(kb.id, page, stats)
            except Exception as e:
                logger.error(f"Failed to index page {page.title}: {e}")
                stats["errors"].append(f"Page {page.title}: {e}")

    async def _index_page(
        self,
        kb_id: int,
        page: ConfluencePage,
        stats: dict,
    ):
        """Index a single page."""
        # Calculate content hash
        content_hash = hashlib.sha256(page.body.encode()).hexdigest()

        # Check if document exists and has changed
        existing = self.db.query(KnowledgeDocument).filter(
            KnowledgeDocument.knowledge_base_id == kb_id,
            KnowledgeDocument.source_id == page.id,
        ).first()

        if existing:
            if existing.content_hash == content_hash:
                stats["documents_unchanged"] += 1
                return

            # Document changed - delete old chunks
            self.db.query(KnowledgeChunk).filter(
                KnowledgeChunk.document_id == existing.id
            ).delete()
            stats["documents_updated"] += 1
            doc = existing
        else:
            # Create new document
            doc = KnowledgeDocument(
                knowledge_base_id=kb_id,
                source_id=page.id,
                source_url=page.url,
                title=page.title,
            )
            self.db.add(doc)
            self.db.flush()
            stats["documents_added"] += 1

        # Update document
        doc.title = page.title
        doc.source_url = page.url
        doc.content_hash = content_hash
        doc.last_synced_at = datetime.utcnow()

        # Chunk the content
        chunks = self.chunker.chunk_with_sections(page.body, title=page.title)

        if not chunks:
            return

        # Generate embeddings
        chunk_texts = [c.text for c in chunks]
        embeddings = await self.embeddings.embed_batch(chunk_texts)

        # Store chunks with embeddings
        for chunk, embedding in zip(chunks, embeddings):
            kb_chunk = KnowledgeChunk(
                document_id=doc.id,
                content=chunk.text,
                chunk_index=chunk.index,
                embedding=embedding,
                chunk_metadata={
                    "title": page.title,
                    "section": chunk.section,
                    "source_url": page.url,
                },
            )
            self.db.add(kb_chunk)
            stats["chunks_created"] += 1

        self.db.commit()

    async def add_manual_document(
        self,
        kb_id: int,
        title: str,
        content: str,
        source_url: Optional[str] = None,
    ) -> KnowledgeDocument:
        """Add a manual document to a knowledge base.

        Args:
            kb_id: Knowledge base ID
            title: Document title
            content: Document text content
            source_url: Optional source URL

        Returns:
            Created KnowledgeDocument
        """
        kb = self.db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        if not kb:
            raise ValueError(f"Knowledge base {kb_id} not found")

        content_hash = hashlib.sha256(content.encode()).hexdigest()

        doc = KnowledgeDocument(
            knowledge_base_id=kb_id,
            source_id=f"manual_{hashlib.md5(title.encode()).hexdigest()[:8]}",
            source_url=source_url,
            title=title,
            content_hash=content_hash,
            last_synced_at=datetime.utcnow(),
        )
        self.db.add(doc)
        self.db.flush()

        # Chunk and embed
        chunks = self.chunker.chunk_with_sections(content, title=title)

        if chunks:
            chunk_texts = [c.text for c in chunks]
            embeddings = await self.embeddings.embed_batch(chunk_texts)

            for chunk, embedding in zip(chunks, embeddings):
                kb_chunk = KnowledgeChunk(
                    document_id=doc.id,
                    content=chunk.text,
                    chunk_index=chunk.index,
                    embedding=embedding,
                    chunk_metadata={
                        "title": title,
                        "section": chunk.section,
                        "source_url": source_url,
                    },
                )
                self.db.add(kb_chunk)

        # Update KB counts
        kb.document_count += 1
        kb.chunk_count += len(chunks)

        self.db.commit()
        return doc

    async def search(
        self,
        query: str,
        knowledge_base_ids: List[int],
        top_k: int = 5,
    ) -> List[dict]:
        """Search for relevant chunks using vector similarity.

        Args:
            query: Search query
            knowledge_base_ids: Knowledge bases to search
            top_k: Number of results to return

        Returns:
            List of search results with content, metadata, and similarity score
        """
        # Generate query embedding
        query_embedding = await self.embeddings.embed_query(query)

        # Use pgvector similarity search
        # Note: This requires the vector extension and proper column type
        # Convert embedding to PostgreSQL array literal format
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        results = self.db.execute(
            text("""
            SELECT
                kc.content,
                kc.chunk_metadata,
                kd.title,
                kd.source_url,
                1 - (kc.embedding <=> CAST(:query_embedding AS vector)) as similarity
            FROM knowledge_chunks kc
            JOIN knowledge_documents kd ON kc.document_id = kd.id
            WHERE kd.knowledge_base_id = ANY(:kb_ids)
            ORDER BY kc.embedding <=> CAST(:query_embedding AS vector)
            LIMIT :top_k
            """),
            {
                "query_embedding": embedding_str,
                "kb_ids": knowledge_base_ids,
                "top_k": top_k,
            }
        ).fetchall()

        results_list = []
        for row in results:
            similarity = 0.0
            if row[4] is not None:
                try:
                    sim = float(row[4])
                    # Handle NaN or infinity
                    import math
                    if math.isnan(sim) or math.isinf(sim):
                        similarity = 0.0
                    else:
                        similarity = sim
                except (ValueError, TypeError):
                    similarity = 0.0

            results_list.append({
                "content": row[0],
                "metadata": row[1],
                "title": row[2],
                "source_url": row[3],
                "similarity": similarity,
            })

        return results_list
