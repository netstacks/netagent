# Local Embedding Server Design

**Date:** 2026-01-08
**Status:** Approved
**Author:** Claude Code with user collaboration

## Problem Statement

The NetAgent knowledge base requires text embeddings for semantic search over Confluence documentation. The Gemini embedding API is unavailable through the corporate Apigee proxy (returns 404), causing all embeddings to be stored as zero vectors, which breaks search functionality.

## Solution Overview

Deploy a local embedding server as a dedicated Docker container running sentence-transformers with the `all-MiniLM-L6-v2` model. This provides fast, reliable embeddings without external API dependencies.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Knowledge      │     │   Embedding      │     │   PostgreSQL    │
│  Indexer        │────▶│   Server         │     │   + pgvector    │
│  (Worker)       │     │   (Port 8002)    │     │                 │
└─────────────────┘     └──────────────────┘     └─────────────────┘
        │                       │                        ▲
        │                       │                        │
        └───────────────────────┴────────────────────────┘
                         Store embeddings
```

**Key characteristics:**
- Dedicated container for embedding generation
- REST API on port 8002 (internal network only)
- CPU-only PyTorch for smaller image size (~2GB vs ~8GB with CUDA)
- Model pre-downloaded at build time for fast startup
- 384-dimensional embeddings zero-padded to 768 for schema compatibility

## Model Selection

**Model:** `all-MiniLM-L6-v2`

| Property | Value |
|----------|-------|
| Native dimensions | 384 |
| Padded dimensions | 768 (matches existing schema) |
| Model size | ~80MB |
| Inference speed | ~100 texts/second (CPU) |
| Quality | Excellent for general English text |

## API Design

### POST /embed

Generate embeddings for a batch of texts.

**Request:**
```json
{
  "texts": ["First document text", "Second document text"],
  "pad_to": 768
}
```

**Response:**
```json
{
  "embeddings": [[0.123, 0.456, ...], [0.789, 0.012, ...]],
  "model": "all-MiniLM-L6-v2",
  "dimensions": 768
}
```

### GET /health

Health check endpoint for Docker and load balancer integration.

**Response:**
```json
{
  "status": "healthy",
  "model": "all-MiniLM-L6-v2",
  "dimensions": 384
}
```

## Client Integration

Replace `GeminiEmbeddings` with a new `EmbeddingsClient` class:

```python
class EmbeddingsClient:
    """Generate text embeddings using local embedding server."""

    def __init__(
        self,
        base_url: str = None,
        dimensions: int = 768,
    ):
        self.base_url = base_url or os.getenv(
            "EMBEDDING_SERVER_URL",
            "http://embedding:8002"
        )
        self.dimensions = dimensions

    async def embed_batch(
        self,
        texts: List[str],
        batch_size: int = 100,
    ) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        # POST to /embed with pad_to=self.dimensions

    async def embed_query(self, query: str) -> List[float]:
        """Generate embedding for a search query."""
        # Same as embed_batch but single text
```

**Integration points:**
- `shared/netagent_core/knowledge/embeddings.py` - New EmbeddingsClient class
- `services/worker/app/tasks/knowledge_indexer.py` - Uses embeddings client
- `shared/netagent_core/knowledge/indexer.py` - Search functionality

## Docker Configuration

### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi==0.109.0 \
    uvicorn[standard]==0.27.0 \
    sentence-transformers==2.2.2 \
    torch==2.1.0 --index-url https://download.pytorch.org/whl/cpu

COPY services/embedding/app /app

# Pre-download model at build time
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

EXPOSE 8002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
```

### docker-compose.yml addition

```yaml
embedding:
  build:
    context: .
    dockerfile: services/embedding/Dockerfile
  environment:
    - MODEL_NAME=all-MiniLM-L6-v2
    - PAD_DIMENSIONS=768
    - PYTHONUNBUFFERED=1
  networks:
    - netagent
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8002/health"]
    interval: 30s
    timeout: 10s
    retries: 3
    start_period: 60s
```

## Implementation Plan

1. Create embedding server service
   - `services/embedding/Dockerfile`
   - `services/embedding/app/main.py`

2. Update docker-compose.yml
   - Add embedding service definition
   - Add EMBEDDING_SERVER_URL to worker service

3. Create EmbeddingsClient class
   - Replace GeminiEmbeddings in embeddings.py
   - Update __init__.py exports

4. Update knowledge indexer
   - Use new EmbeddingsClient
   - Test embedding generation and storage

5. Test end-to-end
   - Index sample Confluence pages
   - Verify embeddings are non-zero
   - Test semantic search functionality

## Trade-offs and Considerations

**Advantages:**
- No external API dependencies
- Fast inference (~100 texts/second)
- Predictable latency
- Works offline/air-gapped

**Limitations:**
- Additional container to manage
- CPU-only limits throughput (sufficient for this use case)
- Model quality slightly below Gemini (acceptable trade-off)

**Future enhancements:**
- GPU support if higher throughput needed
- Model swapping via environment variable
- Caching layer for repeated queries
