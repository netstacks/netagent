FROM python:3.11-slim

WORKDIR /app

COPY services/slack_bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared library
COPY shared /app/shared

# Copy slack bot app
COPY services/slack_bot/app /app

# Set Python path
ENV PYTHONPATH=/app:/app/shared

CMD ["python", "main.py"]
