FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY services/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared library
COPY shared /app/shared

# Copy API app
COPY services/api/app /app

# Set Python path
ENV PYTHONPATH=/app:/app/shared

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
