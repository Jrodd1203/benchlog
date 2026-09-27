FROM python:3.13-slim

# The API shells out to the git CLI for every project operation.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# A fresh container has no git identity, so `git commit` would fail without these.
ENV GIT_AUTHOR_NAME=benchlog GIT_AUTHOR_EMAIL=benchlog@localhost \
    GIT_COMMITTER_NAME=benchlog GIT_COMMITTER_EMAIL=benchlog@localhost \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[server]'

CMD ["sh", "-c", "uvicorn benchlog.server.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
