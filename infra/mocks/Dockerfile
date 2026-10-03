# 확인 필요(첫 기동 전 사용자가 공식 릴리스 노트로 확인)
FROM python:3.12.8-slim-bookworm

# 확인 필요(첫 기동 전 사용자가 공식 릴리스 노트로 확인)
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
