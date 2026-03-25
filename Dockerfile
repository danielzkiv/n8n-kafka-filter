FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Run as non-root
RUN useradd -m -u 1001 appuser
USER appuser

EXPOSE 8080

CMD ["python", "-m", "app.main"]
