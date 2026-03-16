"""Knowledge base indexing tasks."""

import asyncio
import hashlib
import logging
from datetime import datetime
from celery import shared_task

from netagent_core.db import (
    get_db_context,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeChunk,
    Settings,
)
from netagent_core.knowledge.confluence_client import ConfluenceClient
from netagent_core.knowledge.embeddings import EmbeddingsClient

logger = logging.getLogger(__name__)

# Shared embeddings client (initialized lazily)
_embeddings_client = None


def get_embeddings_client() -> EmbeddingsClient:
    """Get or create the embeddings client."""
    global _embeddings_client
    if _embeddings_client is None:
        _embeddings_client = EmbeddingsClient()
    return _embeddings_client


@shared_task
def sync_knowledge_base(kb_id: int):
    """Sync a knowledge base from its source.

    Fetches content from Confluence (or other sources) and indexes it.
    """
    logger.info(f"Starting knowledge base sync: kb_id={kb_id}")

    with get_db_context() as db:
        kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        if not kb:
            logger.error(f"Knowledge base not found: {kb_id}")
            return {"error": "Knowledge base not found"}

        kb.sync_status = "syncing"
        db.commit()

        try:
            if kb.source_type == "confluence":
                result = sync_confluence(db, kb)
            elif kb.source_type == "manual":
                result = {"message": "Manual knowledge base - no sync needed"}
            else:
                result = {"error": f"Unknown source type: {kb.source_type}"}

            kb.sync_status = "completed"
            kb.last_sync_at = datetime.utcnow()

            # Update counts
            kb.document_count = db.query(KnowledgeDocument).filter(
                KnowledgeDocument.knowledge_base_id == kb_id
            ).count()
            kb.chunk_count = db.query(KnowledgeChunk).join(KnowledgeDocument).filter(
                KnowledgeDocument.knowledge_base_id == kb_id
            ).count()

            db.commit()

            logger.info(f"Knowledge base sync completed: kb_id={kb_id}")
            return result

        except Exception as e:
            logger.exception(f"Knowledge base sync failed: {e}")
            kb.sync_status = "failed"
            db.commit()
            return {"error": str(e)}


def sync_confluence(db, kb: KnowledgeBase):
    """Sync content from Confluence.

    Fetches pages from Confluence and indexes them with embeddings.
    """
    config = kb.source_config or {}

    base_url = config.get("base_url")
    username = config.get("username")
    api_token = config.get("api_token")
    space_key = config.get("space_key")
    parent_page_id = config.get("parent_page_id")

    if not all([base_url, username, api_token]):
        return {"error": "Missing Confluence configuration"}

    if not space_key and not parent_page_id:
        return {"error": "Either space_key or parent_page_id is required"}

    # Run async confluence fetch in sync context
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            _sync_confluence_async(db, kb, base_url, username, api_token, space_key, parent_page_id)
        )
    finally:
        loop.close()


async def _sync_confluence_async(
    db,
    kb: KnowledgeBase,
    base_url: str,
    username: str,
    api_token: str,
    space_key: str = None,
    parent_page_id: str = None,
):
    """Async implementation of Confluence sync."""
    client = ConfluenceClient(
        base_url=base_url,
        username=username,
        api_token=api_token,
    )

    pages = []

    try:
        if parent_page_id:
            # Get pages under a parent page (recursive)
            logger.info(f"Fetching page tree under page {parent_page_id}")
            pages = await client.get_page_tree(parent_page_id)
        elif space_key:
            # Get all pages in a space
            logger.info(f"Fetching pages from space {space_key}")
            pages = await client.get_space_pages(space_key)

        logger.info(f"Found {len(pages)} pages to index")

        indexed = 0
        failed = 0

        for page in pages:
            try:
                # Index each page
                await index_document_async(
                    db=db,
                    kb=kb,
                    source_id=page.id,
                    title=page.title,
                    content=page.body,
                    url=page.url,
                )
                indexed += 1
                logger.debug(f"Indexed page: {page.title}")
            except Exception as e:
                logger.error(f"Failed to index page {page.title}: {e}")
                failed += 1

        return {
            "pages_found": len(pages),
            "indexed": indexed,
            "failed": failed,
        }

    except Exception as e:
        logger.exception(f"Confluence sync error: {e}")
        return {"error": str(e)}


async def index_document_async(
    db, kb: KnowledgeBase, source_id: str, title: str, content: str, url: str = None
):
    """Index a document into the knowledge base (async version).

    Creates embeddings using local embedding server and stores in pgvector.
    """
    # Calculate content hash for change detection
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    # Check if document exists
    doc = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.knowledge_base_id == kb.id,
        KnowledgeDocument.source_id == source_id,
    ).first()

    if doc and doc.content_hash == content_hash:
        # No changes
        return doc

    if not doc:
        doc = KnowledgeDocument(
            knowledge_base_id=kb.id,
            source_id=source_id,
            title=title,
            source_url=url,
        )
        db.add(doc)
        db.flush()  # Get doc.id
    else:
        # Delete old chunks
        db.query(KnowledgeChunk).filter(
            KnowledgeChunk.document_id == doc.id
        ).delete()

    doc.content_hash = content_hash
    doc.last_synced_at = datetime.utcnow()

    # Skip empty content
    if not content or not content.strip():
        db.commit()
        return doc

    # Chunk the content
    chunks = chunk_text(content, chunk_size=1000, overlap=200)

    if not chunks:
        db.commit()
        return doc

    # Generate embeddings in batch using Gemini
    embeddings_client = get_embeddings_client()
    try:
        embeddings = await embeddings_client.embed_batch(chunks)
        logger.debug(f"Generated {len(embeddings)} embeddings for {title}")
    except Exception as e:
        logger.error(f"Failed to generate embeddings for {title}: {e}")
        # Use empty embeddings on failure
        embeddings = [None] * len(chunks)

    # Store chunks with embeddings
    for i, (chunk_content, embedding) in enumerate(zip(chunks, embeddings)):
        chunk = KnowledgeChunk(
            document_id=doc.id,
            content=chunk_content,
            chunk_index=i,
            embedding=embedding,
            chunk_metadata={"title": title, "chunk_index": i, "source_url": url},
        )
        db.add(chunk)

    db.commit()
    return doc


def index_document(db, kb: KnowledgeBase, source_id: str, title: str, content: str, url: str = None):
    """Index a document into the knowledge base (sync wrapper).

    Creates embeddings using local embedding server and stores in pgvector.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            index_document_async(db, kb, source_id, title, content, url)
        )
    finally:
        loop.close()


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list:
    """Split text into overlapping chunks.

    Tries to split on sentence boundaries where possible.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        # If not at the end, try to find a good break point
        if end < len(text):
            # Look for sentence end
            for sep in [". ", ".\n", "\n\n", "\n", " "]:
                last_sep = text.rfind(sep, start + chunk_size // 2, end)
                if last_sep > start:
                    end = last_sep + len(sep)
                    break

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = end - overlap

    return chunks


@shared_task
def sync_pending_knowledge_bases():
    """Periodic task to sync knowledge bases that need updating.

    Checks the knowledge_sync_interval setting to determine sync frequency.
    If set to 0, automatic syncing is disabled.
    """
    from datetime import timedelta

    with get_db_context() as db:
        # Get sync interval from settings (default 60 minutes)
        setting = db.query(Settings).filter(Settings.key == "knowledge_sync_interval").first()
        sync_interval_minutes = 60  # Default
        if setting and setting.value:
            sync_interval_minutes = setting.value.get("value", 60)

        # If interval is 0, automatic syncing is disabled
        if sync_interval_minutes == 0:
            logger.info("Knowledge base auto-sync is disabled (interval=0)")
            return {"queued": 0, "disabled": True}

        # Find knowledge bases that need syncing
        interval_ago = datetime.utcnow() - timedelta(minutes=sync_interval_minutes)

        kbs = db.query(KnowledgeBase).filter(
            (KnowledgeBase.last_sync_at < interval_ago) |
            (KnowledgeBase.last_sync_at.is_(None)) |
            (KnowledgeBase.sync_status == "pending")
        ).all()

        for kb in kbs:
            sync_knowledge_base.delay(kb.id)

        logger.info(f"Knowledge base sync check: queued {len(kbs)} bases (interval={sync_interval_minutes}m)")
        return {"queued": len(kbs), "interval_minutes": sync_interval_minutes}
