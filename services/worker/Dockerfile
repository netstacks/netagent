FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY services/worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared library
COPY shared /app/shared

# Copy worker app
COPY services/worker/app /app

# Set Python path
ENV PYTHONPATH=/app:/app/shared

# Default command (can be overridden)
CMD ["celery", "-A", "celery_app", "worker", "--loglevel=info"]
