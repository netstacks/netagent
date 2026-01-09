"""Embedding generation using local embedding server."""

import os
import logging
from typing import List

import httpx

logger = logging.getLogger(__name__)


class EmbeddingsClient:
    """Generate text embeddings using local embedding server.

    This client connects to a local sentence-transformers based embedding
    server running as a separate Docker container. It provides fast, reliable
    embeddings without external API dependencies.

    Usage:
        embeddings = EmbeddingsClient()
        vectors = await embeddings.embed_batch(["text 1", "text 2"])
    """

    def __init__(
        self,
        base_url: str = None,
        dimensions: int = 768,
    ):
        """Initialize embeddings client.

        Args:
            base_url: Embedding server URL (or EMBEDDING_SERVER_URL env var)
            dimensions: Output embedding dimensions (768 for compatibility)
        """
        self.base_url = (
            base_url
            or os.getenv("EMBEDDING_SERVER_URL", "http://embedding:8002")
        ).rstrip("/")
        self.dimensions = dimensions

        logger.info(f"Embedding server URL: {self.base_url}")

    async def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        embeddings = await self.embed_batch([text])
        return embeddings[0] if embeddings else [0.0] * self.dimensions

    async def embed_batch(
        self,
        texts: List[str],
        batch_size: int = 100,
    ) -> List[List[float]]:
        """Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed
            batch_size: Number of texts per API request

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_embeddings = await self._embed_batch_internal(batch)
            all_embeddings.extend(batch_embeddings)

        return all_embeddings

    async def _embed_batch_internal(
        self,
        texts: List[str],
    ) -> List[List[float]]:
        """Make API request to embed a batch of texts."""
        url = f"{self.base_url}/embed"

        body = {
            "texts": texts,
            "pad_to": self.dimensions,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, json=body)

                if not response.is_success:
                    logger.error(
                        f"Embedding API error {response.status_code}: {response.text}"
                    )
                    # Return zero vectors on error
                    return [[0.0] * self.dimensions for _ in texts]

                data = response.json()

            embeddings = data.get("embeddings", [])

            # Verify dimensions and pad/truncate if needed
            result = []
            for vec in embeddings:
                if len(vec) == self.dimensions:
                    result.append(vec)
                elif len(vec) < self.dimensions:
                    vec.extend([0.0] * (self.dimensions - len(vec)))
                    result.append(vec)
                else:
                    result.append(vec[: self.dimensions])

            # If we got fewer embeddings than texts, pad with zeros
            while len(result) < len(texts):
                result.append([0.0] * self.dimensions)

            return result

        except Exception as e:
            logger.error(f"Embedding request failed: {e}")
            return [[0.0] * self.dimensions for _ in texts]

    async def embed_query(self, query: str) -> List[float]:
        """Generate embedding for a search query.

        Args:
            query: Search query text

        Returns:
            Embedding vector
        """
        return await self.embed(query)

    async def health_check(self) -> bool:
        """Check if embedding server is healthy.

        Returns:
            True if server is healthy, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.is_success
        except Exception:
            return False


# Keep GeminiEmbeddings as a fallback/legacy option
class GeminiEmbeddings:
    """Legacy embedding client using Gemini API (deprecated).

    This class is kept for backwards compatibility but is deprecated.
    Use EmbeddingsClient instead which uses the local embedding server.
    """

    def __init__(
        self,
        model: str = "text-embedding-004",
        dimensions: int = 768,
    ):
        """Initialize embeddings client."""
        logger.warning(
            "GeminiEmbeddings is deprecated. Use EmbeddingsClient instead."
        )
        self._client = EmbeddingsClient(dimensions=dimensions)

    async def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        return await self._client.embed(text)

    async def embed_batch(
        self,
        texts: List[str],
        batch_size: int = 100,
    ) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        return await self._client.embed_batch(texts, batch_size)

    async def embed_query(self, query: str) -> List[float]:
        """Generate embedding for a search query."""
        return await self._client.embed_query(query)
