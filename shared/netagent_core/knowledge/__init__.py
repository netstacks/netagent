"""Knowledge base management module."""

from .confluence_client import ConfluenceClient
from .embeddings import EmbeddingsClient, GeminiEmbeddings
from .chunker import TextChunker, Chunk
from .indexer import KnowledgeIndexer

__all__ = [
    "ConfluenceClient",
    "EmbeddingsClient",
    "GeminiEmbeddings",  # Deprecated, kept for backwards compatibility
    "TextChunker",
    "Chunk",
    "KnowledgeIndexer",
]
