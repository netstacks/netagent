"""Local embedding server using sentence-transformers."""

import os
import logging
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
MODEL_NAME = os.getenv("MODEL_NAME", "all-MiniLM-L6-v2")
PAD_DIMENSIONS = int(os.getenv("PAD_DIMENSIONS", "768"))

# Initialize FastAPI
app = FastAPI(
    title="Embedding Server",
    description="Local embedding generation using sentence-transformers",
    version="1.0.0",
)

# Global model instance (loaded on startup)
model = None
native_dimensions = None


class EmbedRequest(BaseModel):
    """Request body for embedding generation."""

    texts: List[str] = Field(..., description="List of texts to embed")
    pad_to: Optional[int] = Field(
        default=None,
        description="Pad embeddings to this dimension with zeros",
    )


class EmbedResponse(BaseModel):
    """Response body for embedding generation."""

    embeddings: List[List[float]] = Field(..., description="Generated embeddings")
    model: str = Field(..., description="Model used for embedding")
    dimensions: int = Field(..., description="Embedding dimensions")


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    model: str
    dimensions: int


@app.on_event("startup")
async def load_model():
    """Load the embedding model on startup."""
    global model, native_dimensions

    logger.info(f"Loading embedding model: {MODEL_NAME}")
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(MODEL_NAME)
        native_dimensions = model.get_sentence_embedding_dimension()
        logger.info(
            f"Model loaded successfully. Native dimensions: {native_dimensions}"
        )
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        raise


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    return HealthResponse(
        status="healthy",
        model=MODEL_NAME,
        dimensions=native_dimensions,
    )


@app.post("/embed", response_model=EmbedResponse)
async def embed(request: EmbedRequest):
    """Generate embeddings for a batch of texts."""
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    if not request.texts:
        raise HTTPException(status_code=400, detail="No texts provided")

    if len(request.texts) > 1000:
        raise HTTPException(
            status_code=400,
            detail="Maximum 1000 texts per request",
        )

    try:
        # Generate embeddings
        embeddings = model.encode(
            request.texts,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

        # Convert to list and optionally pad
        result_embeddings = []
        pad_to = request.pad_to or native_dimensions

        for embedding in embeddings:
            vec = embedding.tolist()

            # Pad with zeros if needed
            if pad_to > len(vec):
                vec.extend([0.0] * (pad_to - len(vec)))
            elif pad_to < len(vec):
                vec = vec[:pad_to]

            result_embeddings.append(vec)

        return EmbedResponse(
            embeddings=result_embeddings,
            model=MODEL_NAME,
            dimensions=pad_to,
        )

    except Exception as e:
        logger.error(f"Embedding generation failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Embedding generation failed: {str(e)}",
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
