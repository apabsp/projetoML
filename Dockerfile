FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

COPY src/ src/
COPY models/ models/

EXPOSE 3000

CMD ["bentoml", "serve", "src.service:DetectaRiscoService", "--host", "0.0.0.0", "--port", "3000"]
