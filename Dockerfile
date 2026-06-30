# Use Python 3.11 slim - matches the version we tested with
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies needed by some Python packages
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies FIRST (for Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Point to the GCS service account credentials JSON
# The file lives at app/worksphere-490606-3667ec0def14.json inside the container
ENV GOOGLE_APPLICATION_CREDENTIALS="/app/app/worksphere-490606-3667ec0def14.json"

# Cloud Run injects PORT env var; default to 8080
EXPOSE 8080

# Start the FastAPI app with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
