"""Knowledge base indexing tasks."""

import hashlib
import logging
from datetime import datetime
from celery import shared_task

from netagent_core.db import (
    get_db_context,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeChunk,
)

logger = logging.getLogger(__name__)


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
    """Sync content from Confluence."""
    config = kb.source_config or {}

    base_url = config.get("base_url")
    username = config.get("username")
    api_token = config.get("api_token")
    space_key = config.get("space_key")
    parent_page_id = config.get("parent_page_id")

    if not all([base_url, username, api_token]):
        return {"error": "Missing Confluence configuration"}

    # TODO: Implement actual Confluence API calls
    # This is a placeholder that shows the intended structure

    # Example Confluence API integration:
    # import httpx
    # from bs4 import BeautifulSoup
    #
    # auth = (username, api_token)
    # headers = {"Accept": "application/json"}
    #
    # if parent_page_id:
    #     # Get page and its children
    #     url = f"{base_url}/rest/api/content/{parent_page_id}/child/page"
    # else:
    #     # Get all pages in space
    #     url = f"{base_url}/rest/api/content?spaceKey={space_key}&type=page"
    #
    # response = httpx.get(url, auth=auth, headers=headers)
    # pages = response.json().get("results", [])
    #
    # for page in pages:
    #     page_id = page["id"]
    #     title = page["title"]
    #
    #     # Get page content
    #     content_url = f"{base_url}/rest/api/content/{page_id}?expand=body.storage"
    #     content_response = httpx.get(content_url, auth=auth, headers=headers)
    #     html_content = content_response.json()["body"]["storage"]["value"]
    #
    #     # Parse HTML and extract text
    #     soup = BeautifulSoup(html_content, "html.parser")
    #     text = soup.get_text(separator="\n", strip=True)
    #
    #     # Index the page
    #     index_document(db, kb, page_id, title, text, page_url)

    logger.info("Confluence sync placeholder - implement actual API integration")
    return {"message": "Confluence sync placeholder"}


def index_document(db, kb: KnowledgeBase, source_id: str, title: str, content: str, url: str = None):
    """Index a document into the knowledge base.

    Creates embeddings and stores in pgvector.
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

    # Chunk the content
    chunks = chunk_text(content, chunk_size=1000, overlap=200)

    # Generate embeddings and store chunks
    for i, chunk_text in enumerate(chunks):
        # TODO: Generate embedding using Gemini
        # embedding = await generate_embedding(chunk_text)
        embedding = None  # Placeholder

        chunk = KnowledgeChunk(
            document_id=doc.id,
            content=chunk_text,
            chunk_index=i,
            embedding=embedding,
            metadata={"title": title, "chunk_index": i},
        )
        db.add(chunk)

    db.commit()
    return doc


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
    """Periodic task to sync knowledge bases that need updating."""
    with get_db_context() as db:
        # Find knowledge bases that need syncing
        # (not synced in last hour or sync_status is pending)
        from datetime import timedelta

        hour_ago = datetime.utcnow() - timedelta(hours=1)

        kbs = db.query(KnowledgeBase).filter(
            (KnowledgeBase.last_sync_at < hour_ago) |
            (KnowledgeBase.sync_status == "pending")
        ).all()

        for kb in kbs:
            sync_knowledge_base.delay(kb.id)

        return {"queued": len(kbs)}
