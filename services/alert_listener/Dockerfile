FROM python:3.11-slim

WORKDIR /app

COPY services/alert_listener/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/alert_listener/app /app/

CMD ["python", "main.py"]
