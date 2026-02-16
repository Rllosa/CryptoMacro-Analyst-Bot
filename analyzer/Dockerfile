# Python Analyzer (Feature Engine + Regime + Alerts)
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml ./
COPY src ./src

# Install dependencies
RUN pip install --no-cache-dir -e .

# Copy configs (needed for thresholds)
COPY ../configs /app/configs

ENV PYTHONUNBUFFERED=1

CMD ["python", "src/main.py"]
