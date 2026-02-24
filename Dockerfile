FROM python:3.12-slim

WORKDIR /app

# Install system deps for Playwright and httpx[http2]
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps

COPY . .

CMD ["python", "main.py"]
