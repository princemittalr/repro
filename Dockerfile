# Repro: Python (FastAPI + Playwright + Chromium) with Node.js (to run the target app) and ffmpeg.
#   docker build -t repro .
#   docker run --env-file .env -p 8000:8000 repro
# Hosts like Render set $PORT; it defaults to 8000.

# Node.js 22 binaries, copied into the final image below.
FROM node:22-bookworm-slim AS node

# Official Playwright image: Python, Chromium and its system libraries, matching versions.
FROM mcr.microsoft.com/playwright/python:v1.64.0-noble

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# Pin Playwright to the version whose browsers ship in the base image.
RUN pip install --no-cache-dir -r requirements.txt playwright==1.64.0

COPY . .
RUN mkdir -p runs uploads && chown -R pwuser:pwuser /app
USER pwuser

ENV PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request, os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health')"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
