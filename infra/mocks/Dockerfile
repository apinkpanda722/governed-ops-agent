# 공식 릴리스 확인(2026-10-04): python 3.12 최신 패치, Debian 13 stable
FROM python:3.12.15-slim-trixie

# 공식 릴리스 확인(2026-10-04): uv 0.12.23(PyPI 최신)
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
