FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY services/frontend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared library
COPY shared /app/shared

# Copy frontend app
COPY services/frontend/app /app

# Copy static files and templates
COPY static /app/static
COPY services/frontend/app/templates /app/templates

# Set Python path
ENV PYTHONPATH=/app:/app/shared

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
