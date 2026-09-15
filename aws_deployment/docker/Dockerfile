# ============================================================
# Tek Juice Data Engine — Dockerfile
# Exposes the application on port 9600
# ============================================================

# --- Base image ---
FROM python:3.11-slim AS base

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libpq-dev \
    libgeos-dev \
    gdal-bin \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# --- Dependency stage ---
FROM base AS deps

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# --- Final stage ---
FROM deps AS final

# Copy full project
COPY . .

# Create non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
RUN chown -R appuser:appgroup /app
USER appuser

# Expose port 9600
EXPOSE 9600

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:9600/health || exit 1

# Start the application on port 9600
CMD ["uvicorn", "services.api_gateway.gateway:app", "--host", "0.0.0.0", "--port", "9600", "--workers", "4", "--loop", "uvloop"]
